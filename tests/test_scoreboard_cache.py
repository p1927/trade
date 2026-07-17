"""Scoreboard cache invalidation tests."""

from __future__ import annotations

import pytest

from trade_integrations.dataflows.index_research.prediction_algorithms.evaluator.scoreboard import (
    scoreboard_needs_refresh,
)
from trade_integrations.dataflows.index_research.prediction_algorithms.track_constants import (
    BACKTEST_TRACK_IDS,
    SCOREBOARD_SCHEMA_VERSION,
)


@pytest.mark.unit
def test_scoreboard_needs_refresh_on_horizon_mismatch():
    report = {
        "schema_version": SCOREBOARD_SCHEMA_VERSION,
        "eval_count": 50,
        "horizon_days": 14,
        "history_days": 730,
        "tracks": {tid: {"eval_count": 10} for tid in BACKTEST_TRACK_IDS},
    }
    assert scoreboard_needs_refresh(report, horizon_days=14) is False
    assert scoreboard_needs_refresh(report, horizon_days=21) is True


@pytest.mark.unit
def test_summarize_track_metrics_hit_miss_counts():
    from trade_integrations.dataflows.index_research.prediction_algorithms.evaluator.scoreboard import (
        summarize_track_metrics,
    )

    eval_rows = [
        {"date": "2026-01-01", "track_id": "naive_zero", "error_pct": 1.0, "direction_hit": True},
        {"date": "2026-01-02", "track_id": "naive_zero", "error_pct": 2.0, "direction_hit": False},
        {"date": "2026-01-03", "track_id": "naive_zero", "error_pct": 0.5, "direction_hit": True},
    ]
    metrics = summarize_track_metrics(eval_rows, "naive_zero")
    assert metrics["eval_count"] == 3
    assert metrics["direction_hit_count"] == 2
    assert metrics["direction_miss_count"] == 1
    assert metrics["direction_hit_rate"] == pytest.approx(0.6667, abs=0.001)


@pytest.mark.unit
def test_enrich_track_metrics_from_daily_backfills_cached_rows():
    from trade_integrations.dataflows.index_research.prediction_algorithms.evaluator.scoreboard import (
        enrich_track_metrics_from_daily,
    )

    report = {
        "tracks": {"quant_ridge": {"track_id": "quant_ridge", "eval_count": 2, "direction_hit_rate": 0.5}},
        "combiners": {"quant_only": {"track_id": "quant_only", "eval_count": 2}},
        "daily_evaluations": [
            {"date": "2026-01-01", "track_id": "quant_ridge", "error_pct": 1.0, "direction_hit": True},
            {"date": "2026-01-02", "track_id": "quant_ridge", "error_pct": 2.0, "direction_hit": False},
            {"date": "2026-01-01", "track_id": "combiner:quant_only", "error_pct": 1.0, "direction_hit": True},
            {"date": "2026-01-02", "track_id": "combiner:quant_only", "error_pct": 2.0, "direction_hit": True},
        ],
    }
    enriched = enrich_track_metrics_from_daily(report)
    assert enriched["tracks"]["quant_ridge"]["direction_hit_count"] == 1
    assert enriched["tracks"]["quant_ridge"]["direction_miss_count"] == 1
    assert enriched["combiners"]["quant_only"]["direction_hit_count"] == 2
    assert enriched["combiners"]["quant_only"]["direction_miss_count"] == 0
