"""Unit tests for options prediction ledger."""

from __future__ import annotations

from datetime import datetime, timezone

import pandas as pd
import pytest

from trade_integrations.dataflows.options_research.prediction_ledger import (
    OptionsPredictionRecord,
    append_options_prediction,
    calibration_confidence_adjustment,
    compute_options_accuracy_metrics,
    load_ledger,
    reconcile_options_predictions,
)


def _sample_record(**overrides) -> OptionsPredictionRecord:
    base = {
        "underlying": "NIFTY",
        "predicted_at": datetime(2026, 7, 1, 9, 30, tzinfo=timezone.utc),
        "expiry_date": "2026-07-10",
        "spot_at_prediction": 24000.0,
        "prediction_view": "range_bound",
        "expected_move_pct": 2.0,
        "strategy_name": "Iron Condor",
        "strategy_score": 0.72,
    }
    base.update(overrides)
    return OptionsPredictionRecord(**base)


@pytest.mark.unit
def test_append_options_prediction_writes_row(tmp_path, monkeypatch):
    monkeypatch.setenv("TRADE_STACK_HUB_DIR", str(tmp_path))

    append_options_prediction(_sample_record())
    ledger = load_ledger()

    assert len(ledger) == 1
    assert ledger.iloc[0]["underlying"] == "NIFTY"
    assert float(ledger.iloc[0]["spot_at_prediction"]) == pytest.approx(24000.0)
    assert pd.isna(ledger.iloc[0]["actual_return_pct"])


@pytest.mark.unit
def test_reconcile_options_predictions_fills_actual(tmp_path, monkeypatch):
    monkeypatch.setenv("TRADE_STACK_HUB_DIR", str(tmp_path))
    append_options_prediction(
        _sample_record(
            expiry_date="2026-07-10",
            spot_at_prediction=24000.0,
            expected_move_pct=2.0,
        )
    )

    monkeypatch.setattr(
        "trade_integrations.dataflows.options_research.prediction_ledger._fetch_close_on",
        lambda target, underlying: 24480.0,
    )

    as_of = datetime(2026, 7, 11, tzinfo=timezone.utc)
    updated = reconcile_options_predictions(as_of=as_of)
    ledger = load_ledger()

    assert updated == 1
    assert float(ledger.iloc[0]["actual_return_pct"]) == pytest.approx(2.0)
    assert bool(ledger.iloc[0]["move_within_expected"]) is True


@pytest.mark.unit
def test_compute_options_accuracy_metrics(tmp_path, monkeypatch):
    monkeypatch.setenv("TRADE_STACK_HUB_DIR", str(tmp_path))

    from trade_integrations.dataflows.options_research.prediction_ledger import save_ledger

    rows = pd.DataFrame(
        [
            {
                "underlying": "NIFTY",
                "predicted_at": "2026-06-01T09:30:00+00:00",
                "expiry_date": "2026-06-06",
                "spot_at_prediction": 24000.0,
                "prediction_view": "bullish",
                "expected_move_pct": 1.5,
                "strategy_name": "Bull Call",
                "strategy_score": 0.7,
                "actual_return_pct": 1.0,
                "move_within_expected": True,
                "direction_correct": True,
                "metadata_json": "{}",
            },
            {
                "underlying": "NIFTY",
                "predicted_at": "2026-06-02T09:30:00+00:00",
                "expiry_date": "2026-06-07",
                "spot_at_prediction": 24100.0,
                "prediction_view": "bearish",
                "expected_move_pct": 1.5,
                "strategy_name": "Bear Put",
                "strategy_score": 0.65,
                "actual_return_pct": 0.5,
                "move_within_expected": True,
                "direction_correct": False,
                "metadata_json": "{}",
            },
        ]
    )
    save_ledger(rows)

    metrics = compute_options_accuracy_metrics(window=14)

    assert metrics["sample_count"] == 2
    assert metrics["move_hit_rate"] == pytest.approx(1.0)
    assert metrics["direction_hit_rate"] == pytest.approx(0.5)


@pytest.mark.unit
def test_calibration_confidence_adjustment_low_sample_returns_zero(tmp_path, monkeypatch):
    monkeypatch.setenv("TRADE_STACK_HUB_DIR", str(tmp_path))
    assert calibration_confidence_adjustment() == 0.0
