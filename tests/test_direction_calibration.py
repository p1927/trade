"""Tests for direction confidence calibration."""

from __future__ import annotations

import pytest

from trade_integrations.dataflows.index_research.direction_calibration import (
    artifact_direction_hit_rate,
    calibrate_direction_confidence,
    load_walk_forward_accuracy,
)


@pytest.mark.unit
def test_calibrate_caps_extreme_logistic_when_oos_near_fifty():
    metrics = {"direction_hit_rate_walk_forward": 0.5294, "regime_direction_hit_rates": {}}
    calibrated = calibrate_direction_confidence(0.999, "range_bound", metrics)
    assert calibrated <= 0.56
    assert calibrated >= 0.5


@pytest.mark.unit
def test_calibrate_uses_regime_bucket_when_present():
    metrics = {
        "direction_hit_rate_walk_forward": 0.5,
        "regime_direction_hit_rates": {
            "high_fear": {"direction_hit_rate": 0.5556},
        },
    }
    calibrated = calibrate_direction_confidence(0.95, "high_fear", metrics)
    assert calibrated <= 0.58
    assert calibrated > 0.52


@pytest.mark.unit
def test_artifact_direction_hit_rate_from_metrics():
    assert artifact_direction_hit_rate({"direction_hit_rate_walk_forward": 0.5294}) == pytest.approx(0.5294)
    assert artifact_direction_hit_rate({}) is None


@pytest.mark.unit
def test_load_walk_forward_accuracy_reads_backtest(monkeypatch):
    from trade_integrations.dataflows.index_research import backtest_runner

    monkeypatch.setattr(
        backtest_runner,
        "load_backtest_report",
        lambda ticker="NIFTY": {
            "eval_count": 17,
            "metrics": {"direction_hit_rate_walk_forward": 0.5294},
        },
    )
    payload = load_walk_forward_accuracy()
    assert payload["eval_count"] == 17
    assert payload["direction_hit_rate_walk_forward"] == pytest.approx(0.5294)
