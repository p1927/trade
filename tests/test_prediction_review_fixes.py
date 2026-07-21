"""Regression tests for prediction deep-study fixes (N-02, N-05, N-06, N-07)."""

from __future__ import annotations

from datetime import date

import pytest

from trade_integrations.dataflows.index_research.models import ConstituentSignal
from trade_integrations.dataflows.index_research.prediction_ledger import build_prediction_metadata
from trade_integrations.dataflows.index_research.scenarios import (
    _count_earnings_within_horizon,
    _has_upcoming_rbi,
)


@pytest.mark.unit
def test_simulate_uses_headline_baseline_without_overrides():
    pytest.importorskip("sklearn")
    from trade_integrations.dataflows.index_research.simulate import simulate_index_prediction

    macro = {
        "oil_brent": 80.0,
        "usd_inr": 83.0,
        "india_vix": 14.0,
        "sp500": 5200.0,
        "index_sentiment": 0.1,
    }
    headline = 2.75
    result = simulate_index_prediction(
        macro_factors=macro,
        spot=24500.0,
        bottom_up_return_pct=0.5,
        horizon_days=14,
        headline_return_pct=headline,
    )
    assert result["baseline_return_pct"] == pytest.approx(headline, abs=1e-3)
    assert result["forecast_path"][0]["baseline_return_pct"] == pytest.approx(0.0, abs=1e-6)
    assert result["forecast_path"][-1]["baseline_return_pct"] == pytest.approx(headline, abs=1e-3)


@pytest.mark.unit
def test_predict_nifty_uses_gated_macro_when_gate_zeros_output(monkeypatch):
    pytest.importorskip("sklearn")
    from trade_integrations.dataflows.index_research.horizon import resolve_horizon
    from trade_integrations.dataflows.index_research.predictor import ModelArtifact, predict_nifty

    artifact = ModelArtifact(
        coefficients={"usd_inr": 0.1},
        intercept=0.0,
        mae=1.5,
        feature_names=["usd_inr"],
        feature_means=[83.0],
        feature_stds=[1.0],
    )
    monkeypatch.setattr(
        "trade_integrations.dataflows.index_research.predictor.load_stored_model_artifact",
        lambda: artifact,
    )
    monkeypatch.setattr(
        "trade_integrations.dataflows.index_research.predictor._predict_macro_delta",
        lambda *a, **k: 4.5,
    )
    monkeypatch.setattr(
        "trade_integrations.dataflows.index_research.regime_gates.predict_macro_delta_gated",
        lambda *a, **k: 0.0,
    )
    monkeypatch.setattr(
        "trade_integrations.dataflows.index_research.predictor.attribute_constituents",
        lambda *a, **k: [],
    )
    monkeypatch.setattr(
        "trade_integrations.dataflows.index_research.predictor.rollup_attribution",
        lambda *a, **k: {"total_contribution_pct": 0.0},
    )
    monkeypatch.setattr(
        "trade_integrations.dataflows.index_research.event_overlay.enrich_macro_with_news_features",
        lambda f, **k: f,
    )
    monkeypatch.setattr(
        "trade_integrations.dataflows.index_research.event_overlay.merge_overlay_into_macro",
        lambda raw, *a, **k: (raw, {"return_pct": 0.0}),
    )
    monkeypatch.setattr(
        "trade_integrations.dataflows.index_research.event_overlay.compute_event_overlay",
        lambda *a, **k: {"return_pct": 0.0},
    )

    result = predict_nifty(
        spot=24500.0,
        signals=[],
        macro_factors={"usd_inr": 83.0},
        horizon=resolve_horizon(14),
        apply_event_overlay=False,
    )
    assert result["raw_macro_delta_pct"] == pytest.approx(0.0, abs=1e-6)
    assert result["macro_delta_pct"] == pytest.approx(0.0, abs=1e-6)


@pytest.mark.unit
def test_count_earnings_skips_null_event_dates(monkeypatch):
    monkeypatch.setattr(
        "trade_integrations.dataflows.index_research.scenarios._today",
        lambda: date(2026, 7, 1),
    )
    signals = [
        ConstituentSignal(
            symbol="RELIANCE",
            weight=0.1,
            events=[{"type": "results", "date": None}],
        ),
        ConstituentSignal(
            symbol="TCS",
            weight=0.08,
            events=[{"type": "earnings", "date": "2026-07-10"}],
        ),
    ]
    assert _count_earnings_within_horizon(signals, horizon_days=14) == 1


@pytest.mark.unit
def test_has_upcoming_rbi_ignores_null_event_dates():
    assert _has_upcoming_rbi({"rbi_events": [{"date": None}]}, horizon_days=14) is False
    assert _has_upcoming_rbi({"repo_rate": 6.5}, horizon_days=14) is True


@pytest.mark.unit
def test_build_prediction_metadata_maps_scenario_fields():
    meta = build_prediction_metadata(
        ticker="NIFTY",
        horizon_name="B",
        refresh="full",
        prediction={"expected_return_pct": 1.0},
        scenarios=[
            {
                "event": "earnings_cluster",
                "outcome": "positive_surprises",
                "probability": 0.35,
                "midpoint_return_pct": 0.8,
            }
        ],
    )
    row = meta["scenarios"][0]
    assert "earnings_cluster" in row["name"]
    assert row["expected_return_pct"] == pytest.approx(0.8)
    assert row["probability"] == pytest.approx(0.35)
