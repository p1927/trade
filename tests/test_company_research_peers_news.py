"""Unit tests for peers and news pipeline stages."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from trade_integrations.dataflows.company_research.market import Market, normalize_ticker
from trade_integrations.dataflows.company_research.sources.news import _extract_headlines, fetch_news
from trade_integrations.dataflows.company_research.sources.peers_in import fetch_peers_in


@pytest.mark.unit
class TestPeersIn:
    def test_yfinance_sector_partial_without_tapetide(self, monkeypatch):
        monkeypatch.delenv("TAPETIDE_TOKEN", raising=False)
        normalized = normalize_ticker("RELIANCE", market_hint=Market.IN)

        with patch(
            "trade_integrations.dataflows.company_research.sources.peers_in._fetch_yfinance_peers",
            return_value={
                "peers": [],
                "sector_context": {"sector": "Energy", "industry": "Oil & Gas", "source": "yfinance"},
                "primary_source": "yfinance",
            },
        ):
            result = fetch_peers_in(normalized)

        assert result.stage == "peers"
        assert result.status in ("partial", "ok", "skipped")


@pytest.mark.unit
class TestNewsStage:
    def test_news_merges_company_block(self, monkeypatch):
        normalized = normalize_ticker("RELIANCE", market_hint=Market.IN)
        fake_md = "# News\n- Reliance beats estimates\n- Sector rally continues"

        with patch(
            "trade_integrations.dataflows.company_research.sources.news._fetch_ticker_news",
            return_value={
                "ticker": "RELIANCE.NS",
                "label": "RELIANCE",
                "markdown": fake_md,
                "headlines": [{"title": "Reliance beats estimates"}],
                "source": "news_aggregator",
            },
        ):
            result = fetch_news(normalized, peers=[], lookback_days=7)

        assert result.stage == "news"
        assert result.status == "ok"
        assert "Reliance beats" in (result.data or {}).get("markdown", "")

    def test_extract_headlines_from_h3_markdown(self):
        md = (
            "## RELIANCE.NS News, from 2026-07-02 to 2026-07-16:\n\n"
            "### RELIANCE Q4 earnings beat (source: searxng)\n"
            "Summary text here.\n"
        )
        headlines = _extract_headlines(md)
        assert len(headlines) == 1
        assert "earnings beat" in headlines[0]["title"]
