"""Tests for execution simulation layer."""

from __future__ import annotations

from trade_integrations.dataflows.index_research.execution_sim.signal_from_track import (
    build_signals_from_eval_rows,
    signal_from_prediction,
)


def test_signal_from_prediction_futures_trend():
    assert signal_from_prediction(1.2, strategy="futures_trend", threshold=0.5) == 1
    assert signal_from_prediction(-0.8, strategy="futures_trend", threshold=0.5) == -1
    assert signal_from_prediction(0.2, strategy="futures_trend", threshold=0.5) == 0


def test_build_signals_from_eval_rows():
    rows = [
        {"date": "2026-01-01", "track_id": "quant_ridge", "predicted_pct": 1.0, "actual_pct": 0.5, "close": 24000},
        {"date": "2026-01-01", "track_id": "macro_only", "predicted_pct": 0.2, "actual_pct": 0.5, "close": 24000},
        {"date": "2026-01-08", "track_id": "quant_ridge", "predicted_pct": -1.2, "actual_pct": -0.3, "close": 23800},
    ]
    signals = build_signals_from_eval_rows(rows, track_id="quant_ridge", threshold=0.5)
    assert len(signals) == 2
    assert signals[0]["position"] == 1
    assert signals[1]["position"] == -1
