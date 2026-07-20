"""Integration tests for the self-learning calibration loop."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock

import numpy as np
import pandas as pd
import pytest

from trade_integrations.dataflows.index_research.calibrator import retrain, should_retrain
from trade_integrations.dataflows.index_research.horizon import resolve_horizon
from trade_integrations.dataflows.index_research.models import IndexResearchDoc, PredictionRecord
from trade_integrations.dataflows.index_research.predictor import (
    load_stored_model_artifact,
    train_macro_ridge,
)
from trade_integrations.dataflows.index_research.prediction_ledger import (
    append_prediction,
    compute_accuracy_metrics,
    load_ledger,
    reconcile_predictions,
)


def _synthetic_aligned_history(rows: int = 45) -> pd.DataFrame:
    rng = np.random.default_rng(11)
    dates = pd.date_range("2025-06-01", periods=rows, freq="D")
    close = 23000 + np.cumsum(rng.normal(0, 30, rows))
    return pd.DataFrame(
        {
            "date": dates.strftime("%Y-%m-%d"),
            "close": close,
            "usd_inr": 83 + rng.normal(0, 0.2, rows),
            "oil_brent": 78 + rng.normal(0, 1.0, rows),
            "india_vix": 14 + rng.normal(0, 0.5, rows),
            "fii_net_5d": rng.normal(400, 150, rows),
        }
    )


def _sample_record(**overrides) -> PredictionRecord:
    base = {
        "predicted_at": datetime(2026, 6, 1, 9, 30, tzinfo=timezone.utc),
        "horizon_days": 5,
        "spot_at_prediction": 24000.0,
        "expected_return_pct": 1.0,
        "range_low": 23600.0,
        "range_high": 24400.0,
    }
    base.update(overrides)
    return PredictionRecord(**base)


@pytest.mark.unit
def test_retrain_from_synthetic_history_stores_artifact(tmp_path, monkeypatch):
    pytest.importorskip("sklearn")
    monkeypatch.setenv("TRADE_STACK_HUB_DIR", str(tmp_path))
    history = _synthetic_aligned_history()

    monkeypatch.setattr(
        "trade_integrations.dataflows.index_research.calibrator.load_aligned_factor_history",
        lambda days=365: history,
    )

    artifact = retrain(horizon_days=14)

    assert artifact is not None
    assert artifact.mae >= 0
    assert artifact.feature_names

    stored = load_stored_model_artifact()
    assert stored is not None
    assert stored.trained_at
    assert stored.horizon_name == "B"


@pytest.mark.unit
def test_self_learning_loop_reconcile_metrics_trigger_retrain(tmp_path, monkeypatch):
    pytest.importorskip("sklearn")
    monkeypatch.setenv("TRADE_STACK_HUB_DIR", str(tmp_path))
    history = _synthetic_aligned_history()

    # Seed model with low MAE baseline so drift detection works.
    horizon = resolve_horizon(14)
    baseline = train_macro_ridge(history, horizon)
    baseline.mae = 1.0
    from trade_integrations.dataflows.index_research.predictor import store_model_artifact

    store_model_artifact(baseline)

    append_prediction(
        _sample_record(
            predicted_at=datetime(2026, 6, 1, 9, 30, tzinfo=timezone.utc),
            horizon_days=5,
            expected_return_pct=5.0,
        )
    )
    append_prediction(
        _sample_record(
            predicted_at=datetime(2026, 6, 2, 9, 30, tzinfo=timezone.utc),
            horizon_days=5,
            expected_return_pct=-4.0,
        )
    )

    nifty_dates = pd.date_range("2026-06-01", periods=12, freq="B").strftime("%Y-%m-%d").tolist()
    nifty_history = pd.DataFrame(
        {
            "date": nifty_dates,
            "close": [24000.0, 24100.0, 24200.0, 24300.0, 24400.0, 25200.0, 25100.0, 25000.0, 24900.0, 23136.0, 23200.0, 23300.0],
        }
    )
    monkeypatch.setattr(
        "trade_integrations.dataflows.index_research.prediction_ledger.load_nifty_history",
        lambda days=400: nifty_history,
    )
    monkeypatch.setattr(
        "trade_integrations.dataflows.index_research.calibrator.load_aligned_factor_history",
        lambda days=365: history,
    )

    as_of = datetime(2026, 6, 15, tzinfo=timezone.utc)
    updated = reconcile_predictions(as_of=as_of)
    assert updated == 2

    metrics = compute_accuracy_metrics(window=14)
    assert metrics["sample_count"] == 2
    assert metrics["mae_pct"] > 1.0

    assert should_retrain(metrics["mae_14d_pct"], baseline_mae=1.0) is True

    new_artifact = retrain(horizon_days=14)
    assert new_artifact is not None
    assert load_stored_model_artifact() is not None

    ledger = load_ledger()
    assert ledger["actual_return_pct"].notna().all()
    assert ledger["direction_correct"].notna().all()


@pytest.mark.unit
def test_calibration_main_reconciles_scores_and_retrains(tmp_path, monkeypatch):
    pytest.importorskip("sklearn")
    monkeypatch.setenv("TRADE_STACK_HUB_DIR", str(tmp_path))

    doc = IndexResearchDoc(
        ticker="NIFTY",
        as_of=datetime.now(timezone.utc),
        accuracy={},
    )
    from trade_integrations.context.hub import save_index_research

    save_index_research(doc)

    reconcile_mock = MagicMock(return_value=2)
    metrics = {"sample_count": 2, "mae_pct": 2.5, "mae_14d_pct": 2.5, "direction_hit_rate": 0.5}
    metrics_mock = MagicMock(return_value=metrics)
    retrain_mock = MagicMock(return_value=MagicMock(mae=1.8))

    monkeypatch.setattr(
        "trade_integrations.dataflows.index_research.calibration_runner.reconcile_predictions",
        reconcile_mock,
    )
    monkeypatch.setattr(
        "trade_integrations.dataflows.index_research.calibration_runner.compute_accuracy_metrics",
        metrics_mock,
    )
    monkeypatch.setattr(
        "trade_integrations.dataflows.index_research.calibration_runner.should_retrain",
        lambda mae: True,
    )
    monkeypatch.setattr(
        "trade_integrations.dataflows.index_research.calibration_runner.retrain",
        retrain_mock,
    )
    monkeypatch.setattr(
        "trade_integrations.dataflows.index_research.news_event_features.backfill_news_event_features",
        lambda **k: {"status": "skipped"},
    )
    monkeypatch.setattr(
        "trade_integrations.dataflows.index_research.news_impact_engine.reconcile_matured_impacts",
        lambda **k: {"status": "skipped"},
    )
    monkeypatch.setattr(
        "trade_integrations.dataflows.index_research.news_shock_calibration.update_shock_calibration",
        lambda **k: {"status": "skipped"},
    )
    monkeypatch.setattr(
        "trade_integrations.dataflows.index_research.news_event_features.evaluate_news_model_gates",
        lambda **k: {"status": "skipped"},
    )

    from trade_integrations.dataflows.index_research.calibration_runner import run_calibration

    summary = run_calibration(backfill_history=False)
    assert summary["reconciled_rows"] == 2
    reconcile_mock.assert_called_once()
    metrics_mock.assert_called_once()
    retrain_mock.assert_called_once()

    from trade_integrations.context.hub import load_index_research_json

    updated = load_index_research_json("NIFTY")
    assert updated is not None
    assert updated.accuracy.get("retrained") is True
    assert updated.accuracy.get("sample_count") == 2
