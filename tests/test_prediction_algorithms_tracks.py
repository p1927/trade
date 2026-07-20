"""Unit tests for forecast lab track wrappers."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest

from trade_integrations.dataflows.index_research.horizon import resolve_horizon
from trade_integrations.dataflows.index_research.models import ConstituentSignal, IndexResearchDoc
from trade_integrations.dataflows.index_research.prediction_algorithms.api import run_forecast_lab
from trade_integrations.dataflows.index_research.prediction_algorithms.context_builder import (
    build_track_context,
    context_from_hub,
)
from trade_integrations.dataflows.index_research.prediction_algorithms.registry import run_all_tracks
from trade_integrations.dataflows.index_research.predictor import ModelArtifact


def _mock_signals() -> list[ConstituentSignal]:
    return [
        ConstituentSignal(
            symbol="RELIANCE",
            weight=0.12,
            sector="Energy",
            sentiment_score=0.15,
            momentum_7d_pct=1.2,
        ),
        ConstituentSignal(
            symbol="TCS",
            weight=0.1,
            sector="IT",
            sentiment_score=-0.05,
            momentum_7d_pct=-0.4,
        ),
    ]


def _mock_macro() -> dict:
    return {
        "usd_inr": 83.2,
        "oil_brent": 82.0,
        "india_vix": 14.5,
        "nifty_return_7d": 0.8,
        "news_material_7d": 2.0,
        "news_war_7d": 0.0,
    }


def _mock_artifact() -> ModelArtifact:
    return ModelArtifact(
        coefficients={"usd_inr": -0.2, "oil_brent": -0.1, "india_vix": -0.05},
        intercept=0.0,
        mae=1.5,
        feature_names=["usd_inr", "oil_brent", "india_vix"],
        trained_at=datetime.now(timezone.utc).isoformat(),
    )


def _base_context(**overrides):
    ctx = build_track_context(
        ticker="NIFTY",
        spot=24500.0,
        horizon_days=14,
        macro_factors=_mock_macro(),
        signals=_mock_signals(),
        scenarios=[
            {"name": "base", "probability": 0.6, "expected_return_pct": 0.5},
            {"name": "risk_off", "probability": 0.4, "expected_return_pct": -1.0},
        ],
        scenario_anchor=0.1,
        as_of_day="2026-07-17",
        prediction_snapshot={"expected_return_pct": 0.4, "view": "bullish"},
    )
    ctx.model_artifact = _mock_artifact()
    for key, val in overrides.items():
        setattr(ctx, key, val)
    return ctx


@pytest.mark.unit
def test_run_all_tracks_returns_core_ids():
    ctx = _base_context()
    tracks = run_all_tracks(ctx)
    for tid in ("quant_ridge", "macro_only", "bottom_up", "scenario_anchor", "naive_zero"):
        assert tid in tracks
        assert tracks[tid].track_id == tid


@pytest.mark.unit
def test_naive_zero_is_zero():
    ctx = _base_context()
    tracks = run_all_tracks(ctx)
    assert tracks["naive_zero"].expected_return_pct == 0.0
    assert tracks["naive_zero"].view == "neutral"


@pytest.mark.unit
def test_scenario_anchor_uses_precomputed_anchor():
    ctx = _base_context(scenario_anchor=0.42)
    tracks = run_all_tracks(ctx)
    assert tracks["scenario_anchor"].expected_return_pct == pytest.approx(0.42)


@pytest.mark.unit
def test_quant_ridge_uses_prediction_snapshot():
    ctx = _base_context(
        prediction_snapshot={
            "expected_return_pct": 1.25,
            "view": "bullish",
            "bottom_up_return_pct": 0.5,
            "macro_delta_pct": 0.75,
        }
    )
    tracks = run_all_tracks(ctx)
    assert tracks["quant_ridge"].expected_return_pct == pytest.approx(1.25)
    assert tracks["quant_ridge"].view == "bullish"


@pytest.mark.unit
def test_run_forecast_lab_tracks_only():
    ctx = _base_context()
    result = run_forecast_lab(ctx, mode="tracks_only")
    assert result.mode == "tracks_only"
    assert result.combiner is None
    assert "quant_ridge" in result.forecast_tracks
    assert result.cause_stress_index is not None


@pytest.mark.unit
def test_run_forecast_lab_combine_mode():
    ctx = _base_context(
        prediction_snapshot={"expected_return_pct": 0.5, "view": "bullish"},
    )
    result = run_forecast_lab(ctx, mode="combine", combiner_id="quant_only")
    assert result.combiner is not None
    assert result.combiner["combiner_id"] == "quant_only"


@pytest.mark.unit
def test_context_from_hub(monkeypatch):
    doc = IndexResearchDoc(
        ticker="NIFTY",
        as_of=datetime.now(timezone.utc),
        horizon={"name": "B", "days": 14},
        spot=24500.0,
        prediction={"expected_return_pct": 0.3, "view": "neutral"},
        global_factors=[
            {"factor": "usd_inr", "value": 83.0},
            {"factor": "india_vix", "value": 15.0},
            {"factor": "nifty_return_7d", "value": 0.5},
        ],
        constituent_signals=[
            {"symbol": "RELIANCE", "weight": 0.12, "sentiment_score": 0.1},
        ],
        scenarios=[{"name": "base", "probability": 1.0, "expected_return_pct": 0.2}],
    )
    monkeypatch.setattr(
        "trade_integrations.dataflows.index_research.prediction_algorithms.context_builder.load_index_research_json",
        lambda _ticker: doc,
    )
    monkeypatch.setattr(
        "trade_integrations.dataflows.index_research.prediction_algorithms.context_builder.load_stored_model_artifact",
        lambda: _mock_artifact(),
    )
    monkeypatch.setattr(
        "trade_integrations.context.hub.load_agent_debate_json",
        lambda _t: None,
    )

    ctx = context_from_hub("NIFTY")
    assert ctx is not None
    assert ctx.spot == 24500.0
    assert ctx.macro_factors.get("usd_inr") == 83.0

    lab = run_forecast_lab(ctx, mode="tracks_only")
    assert lab.forecast_tracks["headline_legacy"]["expected_return_pct"] == pytest.approx(0.3)


@pytest.mark.unit
def test_cause_stress_index_elevated_with_news():
    ctx = _base_context(
        macro_factors={
            **_mock_macro(),
            "news_material_7d": 8.0,
            "news_surprise_7d": 6.0,
            "news_war_7d": 3.0,
            "india_vix": 22.0,
        }
    )
    result = run_forecast_lab(ctx, mode="tracks_only")
    assert result.cause_stress_index >= 30
    assert result.cause_stress_label in {"elevated", "event_driven"}


def _mock_signals_full(count: int = 8) -> list[ConstituentSignal]:
    symbols = ["RELIANCE", "TCS", "INFY", "HDFCBANK", "ICICIBANK", "HINDUNILVR", "ITC", "SBIN"]
    return [
        ConstituentSignal(
            symbol=symbols[i % len(symbols)],
            weight=0.1,
            sector="Test",
            sentiment_score=0.1 * (i % 3 - 1),
            momentum_7d_pct=0.5 * (i % 2),
        )
        for i in range(count)
    ]


@pytest.mark.unit
def test_track_quant_ridge_no_overlay_skips_overlay(monkeypatch):
    calls: list = []

    def _fake_predict(**kwargs):
        calls.append(kwargs)
        overlay = 0.8 if kwargs.get("apply_event_overlay", True) else 0.0
        return {
            "expected_return_pct": 1.0 if kwargs.get("apply_event_overlay", True) else 0.2,
            "view": "bullish" if kwargs.get("apply_event_overlay", True) else "neutral",
            "bottom_up_return_pct": 0.1,
            "macro_delta_pct": 0.9 if kwargs.get("apply_event_overlay", True) else 0.1,
            "event_overlay_pct": overlay,
        }

    monkeypatch.setattr(
        "trade_integrations.dataflows.index_research.prediction_algorithms.tracks.quant_ridge.predict_nifty",
        lambda **kw: _fake_predict(**kw),
    )
    monkeypatch.setattr(
        "trade_integrations.dataflows.index_research.prediction_algorithms.tracks.quant_ridge_no_overlay.predict_nifty",
        lambda **kw: _fake_predict(**kw),
    )
    ctx = _base_context(prediction_snapshot=None)
    tracks = run_all_tracks(ctx, track_ids=["quant_ridge", "quant_ridge_no_overlay"])
    assert tracks["quant_ridge"].expected_return_pct == pytest.approx(1.0)
    assert tracks["quant_ridge_no_overlay"].expected_return_pct == pytest.approx(0.2)
    assert tracks["quant_ridge_no_overlay"].provenance.get("apply_event_overlay") is False
    assert any(c.get("apply_event_overlay") is False for c in calls)


@pytest.mark.unit
def test_track_quant_ridge_calls_predict_nifty(monkeypatch):
    calls: list = []

    def _fake_predict(**kwargs):
        calls.append(kwargs)
        return {"expected_return_pct": 0.6, "view": "bullish", "bottom_up_return_pct": 0.1, "macro_delta_pct": 0.5}

    monkeypatch.setattr(
        "trade_integrations.dataflows.index_research.prediction_algorithms.tracks.quant_ridge.predict_nifty",
        lambda **kw: _fake_predict(**kw),
    )
    ctx = _base_context(prediction_snapshot=None)
    tracks = run_all_tracks(ctx, track_ids=["quant_ridge"])
    assert tracks["quant_ridge"].expected_return_pct == pytest.approx(0.6)
    assert tracks["quant_ridge"].provenance.get("pre_reconcile") is True
    assert len(calls) == 1


@pytest.mark.unit
def test_track_scenario_anchor_independent(monkeypatch):
    monkeypatch.setattr(
        "trade_integrations.dataflows.index_research.prediction_algorithms.tracks.quant_ridge.predict_nifty",
        lambda **kw: (_ for _ in ()).throw(AssertionError("predict_nifty should not run")),
    )
    ctx = _base_context(scenario_anchor=0.33)
    track = run_all_tracks(ctx, track_ids=["scenario_anchor"])["scenario_anchor"]
    assert track.expected_return_pct == pytest.approx(0.33)


@pytest.mark.unit
def test_bottom_up_requires_min_constituents():
    ctx = _base_context()
    track = run_all_tracks(ctx, track_ids=["bottom_up"])["bottom_up"]
    assert track.available is False
    assert track.provenance.get("signal_count") == 2


@pytest.mark.unit
def test_bottom_up_with_full_constituents():
    ctx = _base_context(signals=_mock_signals_full(8))
    track = run_all_tracks(ctx, track_ids=["bottom_up"])["bottom_up"]
    assert track.available is True
    assert track.provenance.get("signal_count") == 8


@pytest.mark.unit
def test_macro_only_no_overlay_skips_event_overlay(monkeypatch):
    calls: list = []

    def _fake_macro(macro_factors, horizon, artifact, **kwargs):
        calls.append(kwargs)
        return 0.3, {"include_event_overlay": kwargs.get("include_event_overlay", True)}

    monkeypatch.setattr(
        "trade_integrations.dataflows.index_research.prediction_algorithms.tracks.macro_only_no_overlay.compute_macro_only_return",
        _fake_macro,
    )
    ctx = _base_context()
    track = run_all_tracks(ctx, track_ids=["macro_only_no_overlay"])["macro_only_no_overlay"]
    assert track.available is True
    assert any(c.get("include_event_overlay") is False for c in calls)


@pytest.mark.unit
def test_equal_weight_3_uses_split_macro_track():
    from trade_integrations.dataflows.index_research.prediction_algorithms.combiners import run_combiner
    from trade_integrations.dataflows.index_research.prediction_algorithms.types import ForecastTrack

    def _t(tid, val):
        return ForecastTrack(track_id=tid, expected_return_pct=val, view="neutral", available=True)

    tracks = {
        "macro_only_no_overlay": _t("macro_only_no_overlay", 1.0),
        "scenario_anchor": _t("scenario_anchor", -1.0),
        "event_overlay": _t("event_overlay", 0.0),
    }
    result = run_combiner("equal_weight_3", tracks)
    assert set(result.tracks_used) == {"macro_only_no_overlay", "scenario_anchor", "event_overlay"}
    assert result.expected_return_pct == pytest.approx(0.0)


@pytest.mark.unit
def test_event_overlay_available_when_disabled(monkeypatch):
    monkeypatch.setattr(
        "trade_integrations.dataflows.index_research.prediction_algorithms.tracks.event_overlay.compute_event_overlay",
        lambda *a, **k: {"return_pct": 0.0, "method": "disabled", "active_topics": []},
    )
    ctx = _base_context()
    track = run_all_tracks(ctx, track_ids=["event_overlay"])["event_overlay"]
    assert track.available is True
    assert track.provenance.get("method") == "disabled"


@pytest.mark.unit
def test_naive_momentum_horizon_aware():
    ctx = _base_context(macro_factors={**_mock_macro(), "nifty_return_14d": 1.1, "nifty_return_7d": 0.3})
    ctx.horizon = resolve_horizon(14)
    track = run_all_tracks(ctx, track_ids=["naive_momentum"])["naive_momentum"]
    assert track.expected_return_pct == pytest.approx(1.1)
    assert track.provenance.get("factor") == "nifty_return_14d"


@pytest.mark.unit
def test_debate_numeric_parses_pct():
    ctx = _base_context(
        debate_payload={
            "rating": 7,
            "final_trade_decision": "Bullish +2.5% over 2 weeks",
            "investment_debate": {"judge_decision": "accumulate"},
        }
    )
    track = run_all_tracks(ctx, track_ids=["debate_numeric"])["debate_numeric"]
    assert track.available is True
    assert track.expected_return_pct == pytest.approx(2.5)
    assert track.backtest_eligible is False


@pytest.mark.unit
def test_headline_legacy_includes_debate_flag():
    ctx = _base_context(
        legacy_prediction={
            "expected_return_pct": 0.15,
            "view": "neutral",
            "reconciled_with_scenarios": True,
            "debate_merged": True,
        }
    )
    track = run_all_tracks(ctx, track_ids=["headline_legacy"])["headline_legacy"]
    assert track.provenance.get("debate_merged") is True
