"""Unit tests for company research market detection."""

from __future__ import annotations

import pytest

from trade_integrations.dataflows.company_research.market import (
    Market,
    detect_market,
    normalize_ticker,
)
from trade_integrations.dataflows.company_research.models import CompanyResearchDoc, StageResult
from trade_integrations.dataflows.company_research.aggregator import run_company_research
from datetime import datetime, timezone


@pytest.mark.unit
class TestDetectMarket:
    def test_nse_suffix(self):
        assert detect_market("RELIANCE.NS") == Market.IN

    def test_bse_suffix(self):
        assert detect_market("RELIANCE.BO") == Market.IN

    def test_indian_index(self):
        assert detect_market("^NSEI") == Market.IN
        assert detect_market("NIFTY") == Market.IN

    def test_plain_india_with_openalgo(self, monkeypatch):
        monkeypatch.setenv("OPENALGO_API_KEY", "test-key")
        monkeypatch.setenv("TRADINGAGENTS_RESEARCH_MARKET_DEFAULT", "IN")
        assert detect_market("RELIANCE") == Market.IN

    def test_plain_us_when_default_us(self, monkeypatch):
        monkeypatch.delenv("OPENALGO_API_KEY", raising=False)
        monkeypatch.setenv("TRADINGAGENTS_RESEARCH_MARKET_DEFAULT", "US")
        assert detect_market("AAPL") == Market.US

    def test_market_hint_overrides(self, monkeypatch):
        monkeypatch.setenv("OPENALGO_API_KEY", "test-key")
        assert detect_market("AAPL", market_hint=Market.US) == Market.US

    def test_dotted_us_ticker(self):
        assert detect_market("BRK.B") == Market.US


@pytest.mark.unit
class TestNormalizeTicker:
    def test_india_plain_symbol(self, monkeypatch):
        monkeypatch.setenv("OPENALGO_API_KEY", "test-key")
        monkeypatch.setenv("TRADINGAGENTS_RESEARCH_MARKET_DEFAULT", "IN")
        norm = normalize_ticker("RELIANCE")
        assert norm.market == Market.IN
        assert norm.base_symbol == "RELIANCE"
        assert norm.openalgo_symbol == "RELIANCE"
        assert norm.openalgo_exchange == "NSE"
        assert norm.yfinance_symbol == "RELIANCE.NS"

    def test_india_bse_suffix(self):
        norm = normalize_ticker("RELIANCE.BO")
        assert norm.market == Market.IN
        assert norm.yfinance_symbol == "RELIANCE.BO"
        assert norm.openalgo_exchange == "BSE"

    def test_us_symbol(self, monkeypatch):
        monkeypatch.setenv("TRADINGAGENTS_RESEARCH_MARKET_DEFAULT", "US")
        norm = normalize_ticker("AAPL", market_hint=Market.US)
        assert norm.market == Market.US
        assert norm.yfinance_symbol == "AAPL"
        assert norm.openalgo_exchange == ""


@pytest.mark.unit
class TestCompanyResearchDoc:
    def test_defaults(self):
        doc = CompanyResearchDoc(
            ticker="RELIANCE",
            as_of=datetime.now(timezone.utc),
            lookahead_days=14,
        )
        assert doc.ticker == "RELIANCE"
        assert doc.peers == []
        assert doc.stages == []

    def test_stage_result(self):
        result = StageResult(
            stage="identity",
            status="ok",
            vendor="yfinance",
            fetched_at=datetime.now(timezone.utc),
            data={"name": "Apple Inc."},
        )
        assert result.status == "ok"
        assert result.data["name"] == "Apple Inc."


@pytest.mark.unit
class TestRunCompanyResearch:
    def test_smoke_pipeline_market_stage(self, monkeypatch):
        monkeypatch.setenv("OPENALGO_API_KEY", "test-key")
        monkeypatch.setenv("TRADINGAGENTS_RESEARCH_MARKET_DEFAULT", "IN")
        doc = run_company_research("RELIANCE", lookahead_days=14)
        assert doc.ticker == "RELIANCE"
        assert doc.market == "IN"
        assert len(doc.stages) >= 1
        assert doc.stages[0].stage == "market"
        assert doc.stages[0].status == "ok"
        # identity stage may be ok/partial/error depending on network — must exist for IN
        identity_stages = [s for s in doc.stages if s.stage == "identity"]
        assert len(identity_stages) == 1
        calendar_stages = [s for s in doc.stages if s.stage == "calendar"]
        assert len(calendar_stages) == 1
