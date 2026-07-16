"""Unit tests for modular cascade package."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from trade_integrations.dataflows.index_research.cascade.blender import blend_rules
from trade_integrations.dataflows.index_research.cascade.engine import build_cascade_overrides
from trade_integrations.dataflows.index_research.cascade.regime_scaler import (
    classify_cascade_regime,
    scale_rules,
)
from trade_integrations.dataflows.index_research.cascade.rule_provider import (
    CalibratedRuleProvider,
    HeuristicRuleProvider,
)
from trade_integrations.dataflows.index_research.cascade.types import (
    CascadeCalibration,
    CascadeSecondaryRule,
)
from trade_integrations.dataflows.index_research.cascade.var_estimator import (
    fit_var1,
    impulse_response,
    prepare_var_matrix,
)


@pytest.mark.unit
def test_heuristic_oil_cascade_unchanged():
    macro = {"oil_brent": 80.0, "usd_inr": 83.0, "india_vix": 14.0}
    provider = HeuristicRuleProvider(regime="calm")
    overrides, applied = build_cascade_overrides(
        "oil_brent", 10.0, macro, cascade=True, rule_provider=provider
    )
    assert overrides["oil_brent"] == pytest.approx(88.0)
    assert overrides["usd_inr"] > 83.0
    assert overrides["india_vix"] > 14.0
    assert len(applied) >= 3


@pytest.mark.unit
def test_regime_scales_secondary_multipliers():
    rules = [
        CascadeSecondaryRule(
            secondary="india_vix",
            multiplier=0.15,
            mode="absolute",
            source="heuristic",
            heuristic_multiplier=0.15,
        )
    ]
    scaled = scale_rules(rules, "crisis")
    assert scaled[0].multiplier == pytest.approx(0.15 * 1.25)


@pytest.mark.unit
def test_blend_rules_averages_heuristic_and_var():
    var_rules = {
        "oil_brent": [
            CascadeSecondaryRule(
                secondary="usd_inr",
                multiplier=0.30,
                mode="relative",
                source="var",
                var_multiplier=0.30,
            )
        ]
    }
    blended = blend_rules("oil_brent", var_rules=var_rules, alpha=0.5)
    usd = next(r for r in blended if r.secondary == "usd_inr")
    assert usd.source == "blended"
    assert usd.multiplier == pytest.approx(0.225)  # 0.5*0.15 + 0.5*0.30


@pytest.mark.unit
def test_calibrated_provider_uses_persisted_rules():
    cal = CascadeCalibration(
        as_of="2026-07-16",
        status="ok",
        rules={
            "oil_brent": [
                {
                    "secondary": "usd_inr",
                    "multiplier": 0.20,
                    "mode": "relative",
                    "source": "blended",
                    "heuristic_multiplier": 0.15,
                    "var_multiplier": 0.25,
                }
            ]
        },
    )
    provider = CalibratedRuleProvider(cal, regime="calm")
    macro = {"oil_brent": 80.0, "usd_inr": 83.0}
    overrides, applied = build_cascade_overrides(
        "oil_brent", 10.0, macro, rule_provider=provider
    )
    assert overrides["usd_inr"] > 83.0
    row = next(r for r in applied if r["factor"] == "usd_inr")
    assert row.get("source") == "blended"
    assert row.get("var_implied_after") is not None
    assert row.get("heuristic_after") is not None


@pytest.mark.unit
def test_var1_fit_and_irf_on_synthetic_data():
    rng = np.random.default_rng(42)
    n = 80
    oil = np.cumsum(rng.normal(0, 0.5, n)) + 80
    inr = 83 + 0.1 * (oil - oil[0]) + rng.normal(0, 0.05, n)
    vix = 14 + 0.05 * (oil - oil[0]) + rng.normal(0, 0.1, n)
    frame = pd.DataFrame(
        {
            "oil_brent": oil,
            "usd_inr": inr,
            "india_vix": vix,
            "sp500": 5000 + rng.normal(0, 5, n),
            "fii_net_5d": rng.normal(0, 500, n),
            "us_10y": 4.2 + rng.normal(0, 0.02, n),
            "nifty_pcr": 1.0 + rng.normal(0, 0.05, n),
        }
    )
    matrix = prepare_var_matrix(frame)
    fit = fit_var1(matrix)
    assert fit is not None
    paths = impulse_response(fit, shock_factor="oil_brent", shock_size=1.0, horizon=3)
    assert "usd_inr" in paths
    assert len(paths["usd_inr"]) >= 1


@pytest.mark.unit
def test_classify_cascade_regime():
    assert classify_cascade_regime(india_vix=12) == "calm"
    assert classify_cascade_regime(india_vix=17) == "elevated"
    assert classify_cascade_regime(india_vix=22) == "crisis"
