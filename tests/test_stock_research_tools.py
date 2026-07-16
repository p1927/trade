"""Unit tests for stock research TradingAgents tool."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import patch

import pytest

from trade_integrations.dataflows.stock_research.models import StockResearchDoc
from trade_integrations.research.orchestrator import ResearchResult
from trade_integrations.research.registry import ResearchKind
from trade_integrations.tools.stock_research_tools import (
    fetch_stock_research_report,
    get_stock_research,
)


@pytest.mark.unit
class TestStockResearchTool:
    def test_index_ticker_returns_message(self):
        result = fetch_stock_research_report("NIFTY")
        assert "not available" in result.lower()

    def test_tool_invokes_orchestrator(self):
        doc = StockResearchDoc(
            ticker="RELIANCE",
            as_of=datetime.now(timezone.utc),
            lookahead_days=14,
            recommended={"name": "event_play", "action": "BUY"},
        )
        result = ResearchResult(status="complete", kind=ResearchKind.STOCK, ticker="RELIANCE", doc=doc)
        with patch(
            "trade_integrations.tools.stock_research_tools.ensure_research_complete",
            return_value=result,
        ) as orch_mock:
            with patch(
                "trade_integrations.tools.stock_research_tools.format_stock_report",
                return_value="# Stock Trade Plan",
            ):
                out = get_stock_research.invoke({"ticker": "RELIANCE"})
        assert "Stock Trade Plan" in out
        orch_mock.assert_called_once()

    def test_refresh_bypasses_cache(self):
        doc = StockResearchDoc(
            ticker="RELIANCE",
            as_of=datetime.now(timezone.utc),
            lookahead_days=14,
        )
        result = ResearchResult(status="complete", kind=ResearchKind.STOCK, ticker="RELIANCE", doc=doc)
        with patch(
            "trade_integrations.tools.stock_research_tools.ensure_research_complete",
            return_value=result,
        ) as orch_mock:
            with patch(
                "trade_integrations.tools.stock_research_tools.format_stock_report",
                return_value="fresh plan",
            ):
                out = fetch_stock_research_report("RELIANCE", use_cache=False)
        assert out == "fresh plan"
        orch_mock.assert_called_once()
        assert orch_mock.call_args.kwargs["refresh"] is True
