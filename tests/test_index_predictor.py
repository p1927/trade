"""Unit tests for hybrid Nifty predictor."""

from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd
import pytest

from trade_integrations.dataflows.index_research.horizon import resolve_horizon
from trade_integrations.dataflows.index_research.models import ConstituentSignal
from trade_integrations.dataflows.index_research.predictor import (
    ModelArtifact,
    load_stored_model_artifact,
    predict_nifty,
    store_model_artifact,
    train_macro_ridge,
)


def _synthetic_history(rows: int = 30) -> pd.DataFrame:
    rng = np.random.default_rng(7)
    horizon = resolve_horizon(14)
    dates = pd.date_range("2026-01-01", periods=rows, freq="D")
    close = 24000 + np.cumsum(rng.normal(0, 25, rows))
    usd_inr = 83 + rng.normal(0, 0.15, rows)
    oil = 80 + rng.normal(0, 0.8, rows)
    fii = rng.normal(500, 120, rows)
    nifty_return_7d = rng.normal(0.5, 1.0, rows)
    nifty_pcr = 1.0 + rng.normal(0, 0.05, rows)
    return pd.DataFrame(
        {
            "date": dates.strftime("%Y-%m-%d"),
            "close": close,
            "usd_inr": usd_inr,
            "oil_brent": oil,
            "fii_net_5d": fii,
            "nifty_return_7d": nifty_return_7d,
            "nifty_pcr": nifty_pcr,
            "days_to_monthly_expiry": np.linspace(20, 1, rows),
            "is_budget_week": np.zeros(rows),
            "is_results_season": np.ones(rows),
        }
    ), horizon


@pytest.mark.unit
def test_train_macro_ridge_walk_forward_and_direction_head():
    pytest.importorskip("sklearn")
    history, horizon = _synthetic_history(rows=40)
    artifact = train_macro_ridge(history, horizon)

    assert artifact.feature_names
    assert artifact.mae >= 0
    assert artifact.horizon_name == "B"
    assert isinstance(artifact.coefficients, dict)
    assert artifact.direction_hit_rate_oos is None or 0.0 <= artifact.direction_hit_rate_oos <= 1.0


@pytest.mark.unit
def test_train_macro_ridge_synthetic():
    pytest.importorskip("sklearn")
    history, horizon = _synthetic_history()
    artifact = train_macro_ridge(history, horizon)

    assert artifact.feature_names
    assert artifact.mae >= 0
    assert artifact.horizon_name == "B"
    assert isinstance(artifact.coefficients, dict)


@pytest.mark.unit
def test_predict_nifty_hybrid(monkeypatch):
    pytest.importorskip("sklearn")
    monkeypatch.setattr(
        "trade_integrations.dataflows.index_research.attribution._today",
        lambda: date(2026, 7, 16),
    )

    history, horizon = _synthetic_history()
    artifact = train_macro_ridge(history, horizon)

    signals = [
        ConstituentSignal(symbol="RELIANCE", weight=0.4, sentiment_score=0.2),
        ConstituentSignal(symbol="TCS", weight=0.3, sentiment_score=-0.1),
    ]
    macro = {"usd_inr": 83.2, "oil_brent": 82.0, "fii_net_5d": 600.0}

    result = predict_nifty(
        spot=24500.0,
        signals=signals,
        macro_factors=macro,
        horizon=horizon,
        model_artifact=artifact,
    )

    assert result["view"] in {"bullish", "bearish", "neutral"}
    assert "direction_view" in result
    assert "direction_confidence" in result
    assert "direction_confidence_raw" in result
    assert result.get("direction_model_score") == result.get("direction_confidence_raw")
    if result.get("direction_confidence_raw") is not None and result.get("direction_confidence") is not None:
        assert result["direction_confidence"] <= max(result["direction_confidence_raw"], 0.56)
    assert "expected_return_pct" in result
    assert "range" in result
    assert result["range"]["low"] < result["range"]["high"]
    assert "coefficients" in result["equation"]
    assert result["bottom_up_return_pct"] != 0.0


@pytest.mark.unit
def test_sign_conflict_forces_neutral_and_lower_confidence():
    from trade_integrations.dataflows.index_research.predictor import apply_sign_conflict_gate

    view, conf, conflict = apply_sign_conflict_gate(
        direction_view="bullish",
        direction_confidence=0.58,
        raw_macro=4.0,
        scenario_anchor_return_pct=-1.5,
        regime_label="range_bound",
        wf_metrics={"direction_hit_rate_walk_forward": 0.53},
    )
    assert conflict is True
    assert view == "neutral"
    assert conf == pytest.approx(0.29)


@pytest.mark.unit
def test_sign_conflict_always_neutral_even_when_high_confidence():
    from trade_integrations.dataflows.index_research.predictor import apply_sign_conflict_gate

    view, conf, conflict = apply_sign_conflict_gate(
        direction_view="bullish",
        direction_confidence=0.75,
        raw_macro=4.0,
        scenario_anchor_return_pct=-1.5,
        regime_label="range_bound",
        wf_metrics={"direction_hit_rate_walk_forward": 0.53},
    )
    assert conflict is True
    assert view == "neutral"
    assert conf == pytest.approx(0.375)


@pytest.mark.unit
def test_macro_trust_multiplier_scales_macro_delta():
    pytest.importorskip("sklearn")
    history, horizon = _synthetic_history()
    artifact = train_macro_ridge(history, horizon)
    macro = {"usd_inr": 83.2, "oil_brent": 82.0, "fii_net_5d": 600.0}
    signals = [
        ConstituentSignal(symbol="RELIANCE", weight=0.4, sentiment_score=0.2),
    ]
    full = predict_nifty(
        spot=24500.0,
        signals=signals,
        macro_factors=macro,
        horizon=horizon,
        model_artifact=artifact,
        macro_trust_multiplier=1.0,
    )
    reduced = predict_nifty(
        spot=24500.0,
        signals=signals,
        macro_factors=macro,
        horizon=horizon,
        model_artifact=artifact,
        macro_trust_multiplier=0.5,
    )
    assert abs(float(reduced.get("macro_delta_pct") or 0.0)) <= abs(
        float(full.get("macro_delta_pct") or 0.0)
    )


@pytest.mark.unit
def test_store_and_load_model_artifact(tmp_path, monkeypatch):
    monkeypatch.setenv("TRADE_STACK_HUB_DIR", str(tmp_path))

    artifact = ModelArtifact(
        coefficients={"usd_inr": 0.42, "oil_brent": -0.18},
        intercept=0.05,
        mae=1.1,
        r2_walk_forward=0.38,
        poly_degree=2,
        feature_names=["usd_inr", "oil_brent"],
        trained_at="2026-07-16T12:00:00Z",
        horizon_name="B",
    )
    store_model_artifact(artifact)

    loaded = load_stored_model_artifact()
    assert loaded is not None
    assert loaded.coefficients["usd_inr"] == pytest.approx(0.42)
    assert loaded.mae == pytest.approx(1.1)
    assert loaded.feature_names == ["usd_inr", "oil_brent"]

    model_path = tmp_path / "_data" / "index_factors" / "model" / "latest.json"
    assert model_path.is_file()
