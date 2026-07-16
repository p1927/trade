"""Tests for hub research context helpers."""

from __future__ import annotations

import pytest

from trade_integrations.bridge.hub_context import (
    format_research_context_for_agent,
    has_strategy_options_to_present,
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

    def test_has_strategy_options_to_present(self):
        assert not has_strategy_options_to_present(None)
        assert not has_strategy_options_to_present({"ranked_strategies": []})
        assert has_strategy_options_to_present(
            {"ranked_strategies": [{"name": "Iron condor"}]}
        )
        assert has_strategy_options_to_present(
            {
                "recommended": {
                    "name": "Iron condor",
                    "legs": [{"side": "BUY", "strike": 24000}],
                }
            }
        )
        assert not has_strategy_options_to_present({"recommended": {"name": "Iron condor"}})

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
        assert "Do not call get_options_trade_widget" in block
        assert "MANDATORY" not in block

    def test_format_research_context_requests_widget_when_strategies_ranked(self):
        block = format_research_context_for_agent(
            {
                "underlying": "NIFTY",
                "asset_type": "options",
                "plan_status": "ready",
                "ranked_strategies": [
                    {"name": "Iron condor", "tier": "A", "score": 0.82},
                    {"name": "Bull call spread", "tier": "B", "score": 0.71},
                ],
                "recommended": {
                    "name": "Iron condor",
                    "legs": [{"side": "SELL", "strike": 24000, "option_type": "PE"}],
                },
            }
        )
        assert "get_options_trade_widget(ticker)" in block
        assert "ranked strategy options" in block
        assert "Do not call get_options_trade_widget for prediction" not in block
