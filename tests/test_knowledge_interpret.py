"""Tests for knowledge interpret layer."""

from __future__ import annotations

import pytest

from trade_integrations.knowledge.interpret import (
    build_index_interpretation_bundle,
    build_technical_interpretation,
    detect_forecast_disagreements,
    resolve_active_strategy_profile,
)


@pytest.mark.unit
def test_build_technical_interpretation_includes_rsi():
    factors = {"nifty_rsi_14": 72.0, "india_vix": 15.0, "nifty_ma20_distance_pct": 1.2}
    text = build_technical_interpretation(factors, trend_20d="up")
    assert "RSI" in text or "rsi" in text.lower()
    assert len(text) > 20


@pytest.mark.unit
def test_resolve_active_strategy_profile_low_vix_momentum():
    factors = {"india_vix": 12.0, "nifty_rsi_14": 58.0, "nifty_adx_14": 28.0}
    profile = resolve_active_strategy_profile(factors, horizon_name="B", trend_20d="up")
    assert profile.get("key") in {
        "momentum",
        "low_vol_carry",
        "mean_reversion",
        "flow_driven",
        "global_risk_off",
        "event_vol",
        "structural",
        "defensive",
    }


@pytest.mark.unit
def test_detect_forecast_disagreements_bearish_ta_vs_bullish_model():
    factors = {"nifty_rsi_14": 75.0, "india_vix": 22.0, "nifty_macd_histogram": -3.0}
    prediction = {"view": "bullish", "expected_return_pct": 2.5}
    rows = detect_forecast_disagreements(factors, prediction, trend_20d="up")
    assert rows


@pytest.mark.unit
def test_resolve_active_strategy_profile_global_risk_off():
    factors = {
        "fii_net_5d": -5000.0,
        "institutional_net_5d": -3000.0,
        "india_vix": 20.0,
        "nifty_return_14d": -3.0,
    }
    profile = resolve_active_strategy_profile(factors, horizon_name="B", trend_20d="down")
    assert profile.get("key") in {"global_risk_off", "defensive", "mean_reversion"}


@pytest.mark.unit
def test_resolve_active_strategy_profile_sector_rotation():
    factors = {"nifty_return_7d": 0.5, "nifty_adx_14": 18.0}
    breadth = {
        "by_sector": {"IT": 0.6, "BANK": -0.2, "FMCG": 0.1, "AUTO": -0.3},
    }
    profile = resolve_active_strategy_profile(
        factors,
        horizon_name="B",
        trend_20d="sideways",
        sector_breadth=breadth,
    )
    assert profile.get("key") in {"sector_rotation", "low_vol_carry", "mean_reversion", "momentum"}


@pytest.mark.unit
def test_build_strategy_context_string():
    from trade_integrations.knowledge.interpret import build_strategy_context_string

    ctx = build_strategy_context_string(
        {
            "label": "Momentum",
            "when": "VIX < 18",
            "logic": "Ride strength",
            "options_handoff": "Bull call spread",
        }
    )
    assert "Momentum" in ctx
    assert "Bull call spread" in ctx


@pytest.mark.unit
def test_build_index_interpretation_bundle_keys():
    factors = {
        "nifty_rsi_14": 55.0,
        "india_vix": 16.0,
        "nifty_ma20_distance_pct": 0.5,
        "nifty_macd_histogram": 1.0,
    }
    bundle = build_index_interpretation_bundle(
        factors,
        horizon_name="B",
        horizon_days=14,
        trend_20d="sideways",
        prediction={"view": "neutral", "expected_return_pct": 0.2},
    )
    assert bundle.get("technical_interpretation")
    assert bundle.get("active_strategy_profile")
    assert bundle.get("strategy_context")
    assert bundle.get("strategy_options_handoff")
    assert isinstance(bundle.get("technical_readings"), dict)
