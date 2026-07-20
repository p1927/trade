"""Regression tests for prediction data consistency fixes."""

from __future__ import annotations

from unittest.mock import patch

import pandas as pd
import pytest


@pytest.mark.unit
def test_merge_flow_columns_overlays_pcr_when_fii_net_present():
    from trade_integrations.dataflows.index_research.panel_enrichment import _merge_flow_columns

    panel = pd.DataFrame(
        {
            "date": ["2026-01-01", "2026-01-02"],
            "fii_net": [100.0, 110.0],
            "nifty_pcr": [pd.NA, pd.NA],
        }
    )
    merged = pd.DataFrame(
        {
            "date": ["2026-01-01", "2026-01-02"],
            "fii_net": [100.0, 110.0],
            "dii_net": [50.0, 55.0],
            "nifty_pcr": [1.05, 1.10],
        }
    )
    with patch(
        "trade_integrations.dataflows.index_research.sources.nse_flow_derivatives_backfill.merge_flow_derivatives_frame",
        return_value=merged,
    ):
        out = _merge_flow_columns(panel, allow_live_fetch=False)

    pcr = pd.to_numeric(out["nifty_pcr"], errors="coerce")
    assert pcr.notna().all()
    assert float(pcr.iloc[0]) == pytest.approx(1.05)
    assert float(pcr.iloc[1]) == pytest.approx(1.10)


@pytest.mark.unit
def test_audit_flow_parity_warns_on_panel_cold_mismatch():
    from trade_integrations.dataflows.index_research.prediction_audit_extensions import audit_flow_parity

    panel = pd.DataFrame({"date": ["2026-01-01", "2026-01-02"], "nifty_pcr": [1.0, pd.NA]})
    nifty = pd.DataFrame({"date": ["2026-01-01", "2026-01-02"], "close": [100.0, 101.0]})
    cold = pd.DataFrame({"date": ["2026-01-01", "2026-01-02"], "nifty_pcr": [1.0, 1.1]})
    flow = pd.DataFrame({"date": ["2026-01-01", "2026-01-02"], "nifty_pcr": [1.0, 1.1]})
    factors = pd.DataFrame(
        {
            "date": ["2026-01-01", "2026-01-02"],
            "factor": ["nifty_pcr", "nifty_pcr"],
            "value": [1.0, 1.1],
        }
    )

    with patch(
        "trade_integrations.dataflows.index_research.sources.history_loader.load_nifty_history",
        return_value=nifty,
    ), patch(
        "trade_integrations.dataflows.index_research.history_store.load_history_dataset",
        return_value=cold,
    ), patch(
        "trade_integrations.dataflows.index_research.factor_store.load_factor_history",
        return_value=factors,
    ), patch(
        "trade_integrations.dataflows.index_research.sources.nse_flow_derivatives_backfill.merge_flow_derivatives_frame",
        return_value=flow,
    ), patch(
        "trade_integrations.dataflows.index_research.sources.nse_flow_derivatives_backfill.pcr_effective_start",
        return_value="2026-01-01",
    ):
        report = audit_flow_parity(panel, days=30, allow_live_fetch=False)

    assert report["parity_ok"] is False
    assert any("panel_vs_cold" in w for w in report["warnings"])
