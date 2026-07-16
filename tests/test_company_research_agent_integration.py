"""Unit tests for TradingAgents company research integration."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

import trade_integrations  # noqa: F401 — apply patches
from trade_integrations.context.hub import prefetch_company_research
from trade_integrations.dataflows.company_research.models import CompanyResearchDoc
from trade_integrations.tools.company_research_tools import (
    fetch_company_research_report,
    get_company_research,
)


@pytest.mark.unit
class TestCompanyResearchTool:
    def test_index_ticker_returns_message(self):
        result = fetch_company_research_report("^NSEI")
        assert "not available" in result.lower()

    def test_tool_invokes_fetch(self):
        doc = CompanyResearchDoc(
            ticker="RELIANCE",
            as_of=datetime.now(timezone.utc),
            lookahead_days=14,
            market="IN",
        )
        with patch(
            "trade_integrations.tools.company_research_tools.is_cache_fresh",
            return_value=False,
        ):
            with patch(
                "trade_integrations.tools.company_research_tools.run_company_research",
                return_value=doc,
            ):
                with patch(
                    "trade_integrations.tools.company_research_tools.save_company_research",
                ) as save_mock:
                    result = fetch_company_research_report("RELIANCE", use_cache=False)
        assert "Company Research: RELIANCE" in result
        save_mock.assert_called_once()


@pytest.mark.unit
class TestTradingGraphPatch:
    def test_news_toolnode_includes_company_research(self):
        from tradingagents.graph.trading_graph import TradingAgentsGraph

        nodes = TradingAgentsGraph._create_tool_nodes(None)
        news_tools = set(nodes["news"].tools_by_name)
        assert "get_company_research" in news_tools
        assert "get_options_research" in news_tools

    def test_news_analyst_module_wires_options_tool(self):
        import trade_integrations.agents.news_analyst as news_mod

        assert news_mod.get_options_research is not None
        src = Path(news_mod.__file__).read_text(encoding="utf-8")
        assert "get_options_research" in src
        assert "get_options_research(ticker" in src


@pytest.mark.unit
class TestPrefetch:
    def test_prefetch_runs_for_stock(self, monkeypatch):
        calls: list[str] = []

        monkeypatch.setenv("TRADINGAGENTS_RESEARCH_PREFETCH", "true")
        monkeypatch.setattr(
            "trade_integrations.tools.company_research_tools.fetch_company_research_report",
            lambda ticker, **kwargs: calls.append(ticker) or "# report",
        )

        assert prefetch_company_research("RELIANCE") is True
        assert calls == ["RELIANCE"]

    def test_prefetch_skipped_for_index(self, monkeypatch):
        calls: list[str] = []
        monkeypatch.setenv("TRADINGAGENTS_RESEARCH_PREFETCH", "true")
        monkeypatch.setattr(
            "trade_integrations.tools.company_research_tools.fetch_company_research_report",
            lambda ticker, **kwargs: calls.append(ticker),
        )

        assert prefetch_company_research("^NSEI") is False
        assert calls == []

    def test_prefetch_disabled(self, monkeypatch):
        monkeypatch.setenv("TRADINGAGENTS_RESEARCH_PREFETCH", "false")
        assert prefetch_company_research("RELIANCE") is False
