"""Unit tests for index research TradingAgents tool."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import patch

import pytest

from trade_integrations.dataflows.index_research.models import IndexResearchDoc
from trade_integrations.research.orchestrator import ResearchResult
from trade_integrations.research.registry import ResearchKind
from trade_integrations.tools.index_research_tools import (
    fetch_index_research_report,
    get_index_research,
    is_index_research_eligible,
)


@pytest.mark.unit
class TestIndexResearchEligibility:
    def test_nifty_eligible(self):
        assert is_index_research_eligible("NIFTY") is True
        assert is_index_research_eligible("nifty") is True

    def test_equity_not_eligible(self):
        assert is_index_research_eligible("RELIANCE") is False


@pytest.mark.unit
class TestIndexResearchTool:
    def test_equity_ticker_returns_message(self):
        result = fetch_index_research_report("RELIANCE")
        assert "not available" in result.lower()

    def test_tool_invokes_orchestrator(self):
        doc = IndexResearchDoc(
            ticker="NIFTY",
            as_of=datetime.now(timezone.utc),
            horizon={"name": "B", "days": 14},
            spot=24500.0,
        )
        result = ResearchResult(status="complete", kind=ResearchKind.INDEX, ticker="NIFTY", doc=doc)
        with patch(
            "trade_integrations.tools.index_research_tools.ensure_research_complete",
            return_value=result,
        ) as orch_mock:
            with patch(
                "trade_integrations.tools.index_research_tools.format_index_report",
                return_value="# Index Research — NIFTY",
            ):
                out = get_index_research.invoke({"ticker": "NIFTY"})
        assert "Index Research" in out
        orch_mock.assert_called_once()

    def test_refresh_bypasses_cache(self):
        doc = IndexResearchDoc(
            ticker="NIFTY",
            as_of=datetime.now(timezone.utc),
            horizon={"name": "B", "days": 14},
            spot=24500.0,
        )
        result = ResearchResult(status="complete", kind=ResearchKind.INDEX, ticker="NIFTY", doc=doc)
        with patch(
            "trade_integrations.tools.index_research_tools.ensure_research_complete",
            return_value=result,
        ) as orch_mock:
            with patch(
                "trade_integrations.tools.index_research_tools.format_index_report",
                return_value="fresh index report",
            ):
                out = fetch_index_research_report("NIFTY", use_cache=False)
        assert out == "fresh index report"
        orch_mock.assert_called_once()
        assert orch_mock.call_args.kwargs["refresh"] is True
