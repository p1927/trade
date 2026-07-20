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
def test_attach_forecast_lab_surfaces_error(monkeypatch):
    from trade_integrations.dataflows.index_research.prediction_algorithms import pipeline_lab

    monkeypatch.setattr(pipeline_lab, "lab_enabled", lambda: True)
    monkeypatch.setattr(pipeline_lab, "lab_mode", lambda: "log")

    def _boom(*a, **k):
        raise RuntimeError("lab exploded")

    monkeypatch.setattr(pipeline_lab, "run_forecast_lab", _boom)
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
    )
    assert "lab exploded" in out.get("forecast_lab_error", "")


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


@pytest.mark.unit
def test_run_index_research_skips_forecast_lab_by_default(monkeypatch):
    pytest.importorskip("sklearn")
    from trade_integrations.dataflows.index_research.aggregator import run_index_research
    from trade_integrations.dataflows.index_research.models import ConstituentSignal

    attach_calls: list[bool] = []

    def fake_attach(prediction, **kwargs):
        attach_calls.append(True)
        return prediction

    monkeypatch.setattr(
        "trade_integrations.dataflows.index_research.prediction_algorithms.pipeline_lab.attach_forecast_lab",
        fake_attach,
    )
    monkeypatch.setattr(
        "trade_integrations.dataflows.index_research.data_completeness.ensure_factor_data_complete",
        lambda **kwargs: {"passes_gate": True, "after": {"min_pct": 95.0}},
    )
    monkeypatch.setattr(
        "trade_integrations.dataflows.index_research.aggregator.batch_constituent_research",
        lambda **_: [
            ConstituentSignal(symbol="RELIANCE", weight=0.5, sentiment_score=0.1, momentum_7d_pct=1.0),
        ],
    )
    monkeypatch.setattr(
        "trade_integrations.dataflows.index_research.aggregator.attach_constituent_momentum",
        lambda signals, **kwargs: signals,
    )
    monkeypatch.setattr(
        "trade_integrations.dataflows.index_research.aggregator.fetch_global_macro_snapshot",
        lambda **_: __import__(
            "trade_integrations.dataflows.company_research.models", fromlist=["StageResult"]
        ).StageResult(
            stage="macro_global",
            status="ok",
            vendor="test",
            fetched_at=__import__("datetime").datetime.now(__import__("datetime").timezone.utc),
            data={"factors": {"usd_inr": 83.0, "india_vix": 14.0}, "factor_rows": []},
            errors=[],
        ),
    )
    monkeypatch.setattr(
        "trade_integrations.dataflows.index_research.aggregator._fetch_spot",
        lambda _: 24500.0,
    )
    monkeypatch.setattr(
        "trade_integrations.dataflows.index_research.aggregator._nifty_trend_20d",
        lambda: "up",
    )
    monkeypatch.setattr(
        "trade_integrations.dataflows.index_research.aggregator.append_prediction",
        lambda *_, **__: None,
    )
    monkeypatch.setattr(
        "trade_integrations.dataflows.index_research.aggregator.compute_accuracy_metrics",
        lambda: {},
    )
    monkeypatch.setattr(
        "trade_integrations.dataflows.news_hub_bridge.refresh_news_impact",
        lambda **kwargs: {"items": [], "summary": {"approved_count": 0}},
    )

    run_index_research("NIFTY", horizon_days=14, refresh_constituents=True, run_forecast_lab=False)
    assert attach_calls == []
