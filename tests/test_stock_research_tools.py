"""Unit tests for stock research TradingAgents tool."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import patch

import pytest

from trade_integrations.dataflows.stock_research.models import StockResearchDoc
from trade_integrations.tools.stock_research_tools import (
    fetch_stock_research_report,
    get_stock_research,
)


@pytest.mark.unit
class TestStockResearchTool:
    def test_index_ticker_returns_message(self):
        result = fetch_stock_research_report("NIFTY")
        assert "not available" in result.lower()

    def test_tool_invokes_fetch(self):
        doc = StockResearchDoc(
            ticker="RELIANCE",
            as_of=datetime.now(timezone.utc),
            lookahead_days=14,
            recommended={"name": "event_play", "action": "BUY"},
        )
        with patch(
            "trade_integrations.tools.stock_research_tools.is_stock_cache_fresh",
            return_value=False,
        ):
            with patch(
                "trade_integrations.tools.stock_research_tools.run_stock_research",
                return_value=doc,
            ):
                with patch(
                    "trade_integrations.tools.stock_research_tools.save_stock_research",
                ) as save_mock:
                    with patch(
                        "trade_integrations.tools.stock_research_tools.format_stock_report",
                        return_value="# Stock Trade Plan",
                    ):
                        out = get_stock_research.invoke({"ticker": "RELIANCE"})
        assert "Stock Trade Plan" in out
        save_mock.assert_called_once_with(doc)

    def test_uses_cache_when_fresh(self):
        with patch(
            "trade_integrations.tools.stock_research_tools.is_stock_cache_fresh",
            return_value=True,
        ):
            with patch(
                "trade_integrations.tools.stock_research_tools.load_stock_research_markdown",
                return_value="cached plan",
            ):
                out = fetch_stock_research_report("RELIANCE")
        assert out == "cached plan"
