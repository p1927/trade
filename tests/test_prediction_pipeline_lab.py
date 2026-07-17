"""Tests for forecast lab pipeline attachment."""

from __future__ import annotations

import pytest


@pytest.mark.unit
def test_snapshot_pre_reconcile_captures_quant_fields():
    from trade_integrations.dataflows.index_research.prediction_algorithms.pipeline_lab import (
        snapshot_legacy_prediction,
        snapshot_pre_reconcile_prediction,
    )

    pred = {
        "expected_return_pct": 1.2,
        "view": "bullish",
        "bottom_up_return_pct": 0.4,
        "macro_delta_pct": 0.8,
        "reconciled_with_scenarios": True,
    }
    pre = snapshot_pre_reconcile_prediction(pred)
    assert pre["expected_return_pct"] == pytest.approx(1.2)
    assert "reconciled_with_scenarios" not in pre

    legacy = snapshot_legacy_prediction(pred)
    assert legacy["debate_merged"] is False
    assert legacy["expected_return_pct"] == pytest.approx(1.2)


@pytest.mark.unit
def test_attach_forecast_lab_stores_context(monkeypatch):
    from trade_integrations.dataflows.index_research.prediction_algorithms import pipeline_lab

    monkeypatch.setattr(pipeline_lab, "lab_enabled", lambda: True)
    monkeypatch.setattr(pipeline_lab, "lab_mode", lambda: "log")

    class FakeResult:
        def to_dict(self):
            return {
                "forecast_tracks": {"quant_ridge": {"expected_return_pct": 0.5, "available": True}},
                "cause_stress_index": 10,
                "cause_stress_label": "calm",
                "active_causes": [],
            }

    monkeypatch.setattr(pipeline_lab, "run_forecast_lab", lambda *a, **k: FakeResult())
    monkeypatch.setattr(
        pipeline_lab,
        "build_track_context",
        lambda **k: object(),
    )

    pred = {"expected_return_pct": 0.5, "view": "neutral", "bottom_up_return_pct": 0.1}
    pre = {"expected_return_pct": 0.4, "view": "neutral"}
    legacy = {"expected_return_pct": 0.45, "view": "neutral", "debate_merged": False}

    out = pipeline_lab.attach_forecast_lab(
        pred,
        ticker="NIFTY",
        spot=24000.0,
        horizon_days=14,
        macro_factors={},
        signals=[],
        scenarios=[],
        scenario_anchor=None,
        as_of_day="2026-07-01",
        pre_reconcile_snapshot=pre,
        legacy_prediction=legacy,
    )
    assert out["forecast_tracks"]["quant_ridge"]["expected_return_pct"] == pytest.approx(0.5)
    assert out["forecast_lab_context"]["pre_reconcile_snapshot"]["expected_return_pct"] == pytest.approx(0.4)
    assert out["forecast_lab_context"]["legacy_prediction"]["debate_merged"] is False


@pytest.mark.unit
def test_combine_mode_does_not_override_headline_without_promotion(monkeypatch):
    from trade_integrations.dataflows.index_research.prediction_algorithms import pipeline_lab

    monkeypatch.setattr(pipeline_lab, "lab_enabled", lambda: True)
    monkeypatch.setattr(pipeline_lab, "lab_mode", lambda: "combine")
    monkeypatch.setattr(pipeline_lab, "_promoted_combiner_active", lambda *a: False)

    class FakeResult:
        def to_dict(self):
            return {
                "forecast_tracks": {},
                "combiner": {"expected_return_pct": 9.9, "view": "bullish"},
                "active_combiner": "shrinkage_50",
            }

    monkeypatch.setattr(pipeline_lab, "run_forecast_lab", lambda *a, **k: FakeResult())
    monkeypatch.setattr(pipeline_lab, "build_track_context", lambda **k: object())

    pred = {"expected_return_pct": 0.5, "view": "neutral"}
    out = pipeline_lab.attach_forecast_lab(
        pred,
        ticker="NIFTY",
        spot=24000.0,
        horizon_days=14,
        macro_factors={},
        signals=[],
        scenarios=[],
        scenario_anchor=None,
        as_of_day="2026-07-01",
        pre_reconcile_snapshot={"expected_return_pct": 0.4},
        legacy_prediction={"expected_return_pct": 0.45, "debate_merged": False},
    )
    assert out["expected_return_pct"] == pytest.approx(0.5)
    assert out.get("combiner_preview") is not None
