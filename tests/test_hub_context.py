"""Tests for hub research context helpers."""

from __future__ import annotations

import pytest

from trade_integrations.bridge.hub_context import (
    format_research_context_for_agent,
    infer_debate_asset_type,
    normalize_strategy_key,
)


@pytest.mark.unit
class TestHubContext:
    def test_normalize_strategy_key(self):
        assert normalize_strategy_key("Bull Call Spread") == "bull_call_spread"

    def test_infer_debate_asset_type_index(self):
        assert infer_debate_asset_type("NIFTY") == "options"
        assert infer_debate_asset_type("RELIANCE", "stock") == "stock"

    def test_format_research_context_includes_warnings(self):
        block = format_research_context_for_agent(
            {
                "underlying": "NIFTY",
                "asset_type": "options",
                "plan_status": "incomplete",
                "data_warnings": ["Live option chain was unavailable"],
                "ranked_strategies": [],
            }
        )
        assert "[research_context]" in block
        assert "incomplete" in block
        assert "Live option chain" in block
        assert "refresh=true" in block
