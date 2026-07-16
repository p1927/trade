"""Unit tests for index prediction ledger and calibrator."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pandas as pd
import pytest

from trade_integrations.dataflows.index_research.calibrator import should_retrain
from trade_integrations.dataflows.index_research.models import PredictionRecord
from trade_integrations.dataflows.index_research.prediction_ledger import (
    append_prediction,
    compute_accuracy_metrics,
    load_ledger,
    reconcile_predictions,
    save_ledger,
)


def _sample_record(**overrides) -> PredictionRecord:
    base = {
        "predicted_at": datetime(2026, 7, 1, 9, 30, tzinfo=timezone.utc),
        "horizon_days": 7,
        "spot_at_prediction": 24000.0,
        "expected_return_pct": 1.0,
        "range_low": 23640.0,
        "range_high": 24360.0,
    }
    base.update(overrides)
    return PredictionRecord(**base)


@pytest.mark.unit
def test_append_prediction_writes_row(tmp_path, monkeypatch):
    monkeypatch.setenv("TRADE_STACK_HUB_DIR", str(tmp_path))

    append_prediction(_sample_record())
    ledger = load_ledger()

    assert len(ledger) == 1
    assert float(ledger.iloc[0]["spot_at_prediction"]) == pytest.approx(24000.0)
    assert float(ledger.iloc[0]["expected_return_pct"]) == pytest.approx(1.0)
    assert pd.isna(ledger.iloc[0]["actual_return_pct"])


@pytest.mark.unit
def test_reconcile_predictions_fills_actual(tmp_path, monkeypatch):
    monkeypatch.setenv("TRADE_STACK_HUB_DIR", str(tmp_path))

    predicted_at = datetime(2026, 6, 1, 9, 30, tzinfo=timezone.utc)
    append_prediction(
        _sample_record(
            predicted_at=predicted_at,
            horizon_days=5,
            spot_at_prediction=24000.0,
            expected_return_pct=2.0,
        )
    )

    history = pd.DataFrame(
        {
            "date": ["2026-06-01", "2026-06-06"],
            "close": [24000.0, 24480.0],
        }
    )
    monkeypatch.setattr(
        "trade_integrations.dataflows.index_research.prediction_ledger.load_nifty_history",
        lambda days=400: history,
    )
    monkeypatch.setattr(
        "trade_integrations.dataflows.index_research.prediction_ledger.datetime",
        __import__("datetime").datetime,
    )

    as_of = datetime(2026, 6, 10, tzinfo=timezone.utc)
    updated = reconcile_predictions(as_of=as_of)
    ledger = load_ledger()

    assert updated == 1
    assert float(ledger.iloc[0]["actual_return_pct"]) == pytest.approx(2.0)
    assert bool(ledger.iloc[0]["direction_correct"]) is True


@pytest.mark.unit
def test_compute_accuracy_metrics(tmp_path, monkeypatch):
    monkeypatch.setenv("TRADE_STACK_HUB_DIR", str(tmp_path))

    rows = pd.DataFrame(
        [
            {
                "predicted_at": "2026-06-01T09:30:00+00:00",
                "horizon_days": 5,
                "spot_at_prediction": 24000.0,
                "expected_return_pct": 1.0,
                "range_low": 23600.0,
                "range_high": 24400.0,
                "actual_return_pct": 1.5,
                "direction_correct": True,
                "metadata_json": "{}",
            },
            {
                "predicted_at": "2026-06-02T09:30:00+00:00",
                "horizon_days": 5,
                "spot_at_prediction": 24100.0,
                "expected_return_pct": -1.0,
                "range_low": 23700.0,
                "range_high": 24500.0,
                "actual_return_pct": 0.5,
                "direction_correct": False,
                "metadata_json": "{}",
            },
        ]
    )
    save_ledger(rows)

    metrics = compute_accuracy_metrics(window=14)

    assert metrics["sample_count"] == 2
    assert metrics["mae_pct"] == pytest.approx(1.0)
    assert metrics["direction_hit_rate"] == pytest.approx(0.5)


@pytest.mark.unit
def test_should_retrain_on_drift():
    assert should_retrain(2.0, baseline_mae=1.5) is True
    assert should_retrain(1.6, baseline_mae=1.5) is False
    assert should_retrain(None, baseline_mae=1.5) is False
