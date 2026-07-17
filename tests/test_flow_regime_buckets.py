"""Tests for flow regime bucket adjustments."""

from __future__ import annotations

import pytest

from trade_integrations.dataflows.index_research.flow_regime_buckets import (
    apply_flow_regime_adjustment,
    flow_regime_bucket,
)


@pytest.mark.unit
def test_range_bound_fii_selling_adds_contrarian_offset():
    adjusted = apply_flow_regime_adjustment(
        -1.0,
        {"fii_net_5d": -5000.0, "dii_absorption_ratio": 0.8},
        "range_bound",
    )
    assert adjusted > -1.0


@pytest.mark.unit
def test_trend_down_no_flow_adjustment():
    adjusted = apply_flow_regime_adjustment(
        -2.0,
        {"fii_net_5d": -5000.0},
        "trend_down",
    )
    assert adjusted == pytest.approx(-2.0)


@pytest.mark.unit
def test_flow_regime_bucket_labels():
    assert flow_regime_bucket({"fii_net_5d": -100.0, "dii_absorption_ratio": 1.5}, "range_bound") == (
        "range_fii_sell_dii_absorb"
    )
