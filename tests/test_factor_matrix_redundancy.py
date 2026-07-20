"""Tests for factor matrix redundancy pruning."""

from __future__ import annotations

import pandas as pd
import pytest

from trade_integrations.dataflows.index_research.factor_matrix import (
    _EXCLUDED_REDUNDANT,
    _apply_redundancy_prune,
    _select_macro_columns,
)
from trade_integrations.dataflows.index_research.horizon import resolve_horizon


@pytest.mark.unit
def test_apply_redundancy_prune_drops_pair_members():
    cols = ["oil_brent", "oil_wti", "nifty_return_7d", "constituent_momentum_7d"]
    pruned = _apply_redundancy_prune(cols)
    assert "oil_wti" not in pruned
    assert "constituent_momentum_7d" not in pruned
    assert "oil_brent" in pruned
    assert "nifty_return_7d" in pruned


@pytest.mark.unit
def test_apply_redundancy_prune_prefers_research_primary_in_groups():
    cols = [
        "india_term_spread",
        "india_credit_spread",
        "india_10y",
        "nifty_stoch_k",
        "nifty_williams_r",
        "nifty_macd_histogram",
        "nifty_macd_line",
        "fii_net_5d",
        "institutional_net_5d",
    ]
    pruned = _apply_redundancy_prune(cols)
    assert pruned == [
        "india_term_spread",
        "nifty_stoch_k",
        "nifty_macd_histogram",
        "fii_net_5d",
    ]


@pytest.mark.unit
def test_apply_redundancy_prune_keeps_observed_credit_spread_with_term_spread():
    cols = ["india_term_spread", "india_credit_spread", "india_10y", "india_91d_tbill"]
    pruned = _apply_redundancy_prune(cols, credit_spread_observed=True)
    assert "india_term_spread" in pruned
    assert "india_credit_spread" in pruned
    assert "india_10y" not in pruned
    assert "india_91d_tbill" not in pruned


@pytest.mark.unit
def test_select_macro_columns_excludes_redundant():
    horizon = resolve_horizon(14)
    frame = pd.DataFrame(
        {
            "date": ["2026-01-01", "2026-01-02"],
            "close": [24000.0, 24100.0],
            "oil_brent": [80.0, 81.0],
            "oil_wti": [79.0, 80.0],
            "sector_breadth_mean_sentiment": [0.1, 0.2],
            "fii_net_5d": [100.0, 110.0],
            "nifty_return_7d": [0.5, 0.6],
            "constituent_momentum_7d": [0.5, 0.6],
        }
    )
    selected = _select_macro_columns(frame, horizon)
    assert "oil_wti" not in selected
    assert "sector_breadth_mean_sentiment" not in selected
    assert "constituent_momentum_7d" not in selected
    for bad in _EXCLUDED_REDUNDANT:
        assert bad not in selected
