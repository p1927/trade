"""Tests for correlated factor cascade in the impact workbench."""

from __future__ import annotations

import pytest

from trade_integrations.dataflows.index_research.cascade import (
    build_cascade_overrides,
    overrides_from_event_preset,
)


@pytest.mark.unit
def test_oil_shock_cascades_usd_inr_and_vix():
    macro = {"oil_brent": 80.0, "usd_inr": 83.0, "india_vix": 14.0}
    overrides, applied = build_cascade_overrides("oil_brent", 10.0, macro, cascade=True)
    assert overrides["oil_brent"] == pytest.approx(88.0)
    assert overrides["usd_inr"] > 83.0
    assert overrides["india_vix"] > 14.0
    assert len(applied) >= 3
    factors = {row["factor"] for row in applied}
    assert "usd_inr" in factors
    assert "india_vix" in factors


@pytest.mark.unit
def test_zero_shock_returns_primary_only():
    macro = {"oil_brent": 80.0, "usd_inr": 83.0}
    overrides, applied = build_cascade_overrides("oil_brent", 0.0, macro)
    assert overrides["oil_brent"] == 80.0
    assert len(applied) == 1


@pytest.mark.unit
def test_cascade_disabled():
    macro = {"oil_brent": 80.0, "usd_inr": 83.0, "india_vix": 14.0}
    overrides, applied = build_cascade_overrides("oil_brent", 10.0, macro, cascade=False)
    assert "usd_inr" not in overrides
    assert len(applied) == 1


@pytest.mark.unit
def test_event_preset_overrides():
    macro = {"oil_brent": 80.0, "usd_inr": 83.0, "india_vix": 14.0}
    curves = [
        {
            "event": "oil_spike",
            "outcome": "supply_shock",
            "primary_factor": "oil_brent",
            "factor_shocks": {"oil_brent": 0.10, "usd_inr": 0.015, "india_vix": 1.5},
        }
    ]
    overrides, applied = overrides_from_event_preset(curves, "oil_spike|supply_shock", macro)
    assert overrides["oil_brent"] == pytest.approx(88.0)
    assert overrides["india_vix"] == pytest.approx(15.5)
    assert len(applied) == 3
