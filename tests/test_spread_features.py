"""Unit tests for Phase I spread / velocity features."""

from __future__ import annotations

import pandas as pd
import pytest

from trade_integrations.dataflows.index_research.spread_features import (
    compute_credit_spread_proxy,
    compute_velocity_3d,
    enrich_spread_columns,
)


@pytest.mark.unit
def test_velocity_3d():
    series = pd.Series([100.0, 105.0, 110.0, 110.0])
    vel = compute_velocity_3d(series)
    # 3d return from index 0 to 3: (110 - 100) / 100 = 10%
    assert vel.iloc[3] == pytest.approx(10.0, rel=0.01)


@pytest.mark.unit
def test_enrich_spread_columns():
    frame = pd.DataFrame(
        {
            "date": [f"2026-07-{10 + i}" for i in range(6)],
            "india_vix": [14.0, 15.0, 16.0, 17.0, 18.0, 20.0],
            "usd_inr": [83.0, 83.1, 83.2, 83.3, 83.5, 84.0],
            "fii_net_5d": [1000.0, 1100.0, 1200.0, 900.0, 700.0, 500.0],
        }
    )
    out = enrich_spread_columns(frame)
    assert "india_vix_velocity_3d" in out.columns
    assert "usd_inr_momentum_5d" in out.columns
    assert "fii_net_5d_momentum" in out.columns
    assert out["fii_net_5d_momentum"].iloc[-1] == pytest.approx(-500.0)


@pytest.mark.unit
def test_credit_spread_proxy_from_term_spread():
    frame = pd.DataFrame(
        {
            "date": ["2026-07-10", "2026-07-11"],
            "india_10y": [7.2, 7.4],
            "india_91d_tbill": [6.5, 6.5],
        }
    )
    out = enrich_spread_columns(frame)
    assert out["india_credit_spread"].iloc[0] == pytest.approx(0.576, rel=0.01)
    assert float(compute_credit_spread_proxy(1.0)) == pytest.approx(0.63, rel=0.01)
