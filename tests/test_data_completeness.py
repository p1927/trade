"""Tests for flow data completeness gate."""

from __future__ import annotations

from unittest.mock import patch

import pandas as pd
import pytest

from trade_integrations.dataflows.index_research.data_completeness import (
    MIN_FLOW_COVERAGE_PCT,
    measure_flow_coverage,
)
from trade_integrations.dataflows.index_research.ml_experiments_defer import (
    PHASE3_OOS_GATE_DIRECTION_PCT,
    phase3_gate_passed,
    should_run_experiment,
)


@pytest.mark.unit
def test_ensure_factor_data_complete_enrich_uses_cached_only_by_default():
    with patch(
        "trade_integrations.dataflows.index_research.data_completeness.measure_flow_coverage",
        return_value={"passes_gate": False, "min_pct": 50.0},
    ), patch(
        "trade_integrations.dataflows.index_research.factor_backfill_enrichment.enrich_factor_history",
        return_value={"days_enriched": 1},
    ) as enrich_mock:
        from trade_integrations.dataflows.index_research.data_completeness import (
            ensure_factor_data_complete,
        )

        result = ensure_factor_data_complete(enrich=True)

    assert result["enriched"] is True
    enrich_mock.assert_called_once()
    assert enrich_mock.call_args.kwargs.get("allow_live_fetch") is False


@pytest.mark.unit
def test_ensure_factor_data_complete_skips_enrich_when_disabled():
    with patch(
        "trade_integrations.dataflows.index_research.data_completeness.measure_flow_coverage",
        return_value={"passes_gate": False, "min_pct": 50.0},
    ) as measure_mock, patch(
        "trade_integrations.dataflows.index_research.factor_backfill_enrichment.enrich_factor_history",
    ) as enrich_mock:
        from trade_integrations.dataflows.index_research.data_completeness import (
            ensure_factor_data_complete,
        )

        result = ensure_factor_data_complete(enrich=False)

    assert result["skipped_enrich"] is True
    assert result["enriched"] is False
    assert measure_mock.call_count == 2
    assert measure_mock.call_args.kwargs.get("allow_live_fetch") is False
    enrich_mock.assert_not_called()


@pytest.mark.unit
def test_measure_flow_coverage_passes_when_all_factors_full():
    nifty = pd.DataFrame({"date": ["2026-01-01", "2026-01-02"], "close": [100.0, 101.0]})
    factors = pd.DataFrame(
        {
            "date": ["2026-01-01", "2026-01-02"] * 3,
            "factor": ["fii_net_5d", "dii_net_5d", "nifty_pcr"] * 2,
            "value": [1.0, 2.0, 0.8, 1.1, 2.1, 0.9],
        }
    )
    flow = pd.DataFrame({"date": ["2026-01-01", "2026-01-02"], "fii_net": [1.0, 1.1], "dii_net": [2.0, 2.1]})
    with patch(
        "trade_integrations.dataflows.index_research.data_completeness.load_nifty_history",
        return_value=nifty,
    ), patch(
        "trade_integrations.dataflows.index_research.data_completeness.load_factor_history",
        return_value=factors,
    ), patch(
        "trade_integrations.dataflows.index_research.sources.nse_flow_derivatives_backfill.merge_flow_derivatives_frame",
        return_value=flow,
    ), patch(
        "trade_integrations.dataflows.index_research.sources.nse_flow_derivatives_backfill.flow_effective_start",
        return_value="2026-01-01",
    ):
        report = measure_flow_coverage(days=30)

    assert report["passes_gate"] is True
    assert report["min_pct"] >= MIN_FLOW_COVERAGE_PCT


@pytest.mark.unit
def test_measure_flow_coverage_pcr_uses_separate_threshold():
    nifty = pd.DataFrame({"date": ["2026-01-01", "2026-01-02"], "close": [100.0, 101.0]})
    factors = pd.DataFrame(
        {
            "date": ["2026-01-01", "2026-01-02"] * 3,
            "factor": ["fii_net_5d", "dii_net_5d", "nifty_pcr"] * 2,
            "value": [1.0, 2.0, 0.8, 1.1, 2.1, 0.9],
        }
    )
    flow = pd.DataFrame({"date": ["2026-01-01", "2026-01-02"], "fii_net": [1.0, 1.1], "dii_net": [2.0, 2.1]})
    with patch(
        "trade_integrations.dataflows.index_research.data_completeness.load_nifty_history",
        return_value=nifty,
    ), patch(
        "trade_integrations.dataflows.index_research.data_completeness.load_factor_history",
        return_value=factors,
    ), patch(
        "trade_integrations.dataflows.index_research.sources.nse_flow_derivatives_backfill.merge_flow_derivatives_frame",
        return_value=flow,
    ), patch(
        "trade_integrations.dataflows.index_research.sources.nse_flow_derivatives_backfill.flow_effective_start",
        return_value="2026-01-01",
    ), patch(
        "trade_integrations.dataflows.index_research.sources.nse_flow_derivatives_backfill.pcr_effective_start",
        return_value="2026-01-01",
    ):
        report = measure_flow_coverage(days=30)

    pcr = report["factors"]["nifty_pcr"]
    assert pcr["gate_threshold_pct"] == 90.0
    assert pcr["passes_gate"] is True


@pytest.mark.unit
def test_ml_experiments_defer_gated(monkeypatch):
    monkeypatch.setenv("INDEX_PREDICTION_EXPERIMENTAL_TRACKS", "0")
    monkeypatch.setenv("INDEX_PREDICTION_ML_WALKFORWARD", "0")
    assert should_run_experiment("lightgbm_ensemble", direction_oos_pct=45.0) is True
    assert should_run_experiment("lightgbm_ensemble", direction_oos_pct=48.0) is False
    assert should_run_experiment("quantmuse_import", direction_oos_pct=40.0) is False
    assert phase3_gate_passed(PHASE3_OOS_GATE_DIRECTION_PCT) is True


@pytest.mark.unit
def test_ml_experiments_run_parallel_when_enabled_by_default(monkeypatch):
    monkeypatch.delenv("INDEX_PREDICTION_EXPERIMENTAL_TRACKS", raising=False)
    monkeypatch.delenv("INDEX_PREDICTION_ML_WALKFORWARD", raising=False)
    assert should_run_experiment("lightgbm_ensemble", direction_oos_pct=50.0) is True
