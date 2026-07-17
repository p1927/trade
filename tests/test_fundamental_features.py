"""Unit tests for Phase I fundamental features."""

from __future__ import annotations

import pandas as pd
import pytest

from trade_integrations.dataflows.index_research.fundamental_features import (
    compute_earnings_yield_from_pe,
    compute_equity_risk_premium,
    enrich_fundamental_columns,
)


@pytest.mark.unit
def test_earnings_yield_from_pe():
    pe = pd.Series([20.0, 25.0])
    ey = compute_earnings_yield_from_pe(pe)
    assert ey.iloc[0] == pytest.approx(5.0)
    assert ey.iloc[1] == pytest.approx(4.0)


@pytest.mark.unit
def test_equity_risk_premium():
    ey = pd.Series([5.0, 4.0])
    bond = pd.Series([6.5, 6.0])
    erp = compute_equity_risk_premium(ey, bond)
    assert erp.iloc[0] == pytest.approx(-1.5)
    assert erp.iloc[1] == pytest.approx(-2.0)


@pytest.mark.unit
def test_enrich_fundamental_columns_adds_erp_and_term_spread():
    frame = pd.DataFrame(
        {
            "date": ["2026-07-15", "2026-07-16"],
            "nifty_pe": [22.0, 21.0],
            "india_10y": [7.0, 7.1],
            "india_91d_tbill": [6.0, 6.0],
        }
    )
    out = enrich_fundamental_columns(frame)
    assert "nifty_earnings_yield" in out.columns
    assert out["nifty_earnings_yield"].iloc[0] == pytest.approx(100 / 22)
    assert "equity_risk_premium" in out.columns
    assert "india_term_spread" in out.columns
    assert out["india_term_spread"].iloc[0] == pytest.approx(1.0)
