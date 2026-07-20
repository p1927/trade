"""Unit tests for fundamentals, filings, and macro pipeline stages."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from trade_integrations.dataflows.company_research.market import Market, normalize_ticker
from trade_integrations.dataflows.company_research.sources.filings_in import fetch_filings_in
from trade_integrations.dataflows.company_research.sources.fundamentals_in import fetch_fundamentals_in
from trade_integrations.dataflows.company_research.sources.macro_in import fetch_macro_in


@pytest.mark.unit
class TestFundamentalsIn:
    def test_merges_yfinance_ratios(self, monkeypatch):
        normalized = normalize_ticker("RELIANCE", market_hint=Market.IN)
        monkeypatch.setattr(
            "trade_integrations.dataflows.company_research.sources.fundamentals_in.resolve_bse_scrip_code",
            lambda _sym: None,
        )

        with patch(
            "trade_integrations.dataflows.company_research.sources.fundamentals_in._fetch_yfinance",
            return_value={
                "source": "yfinance",
                "pe_ratio": 25.5,
                "pb_ratio": 2.1,
                "eps": 62.0,
            },
        ):
            result = fetch_fundamentals_in(normalized)

        assert result.stage == "fundamentals"
        assert result.status in ("ok", "partial")
        assert result.data.get("ratios", {}).get("pe_ratio") == 25.5


@pytest.mark.unit
class TestFilingsIn:
    def test_dedupes_filings(self, monkeypatch):
        normalized = normalize_ticker("RELIANCE", market_hint=Market.IN)
        rows = [
            {
                "date": "2026-07-10",
                "description": "Board meeting outcome",
                "type": "announcement",
                "source": "bse_india",
            },
            {
                "date": "2026-07-10",
                "description": "Board meeting outcome",
                "type": "announcement",
                "source": "dalal_bse",
            },
        ]

        with patch(
            "trade_integrations.dataflows.company_research.sources.filings_in._fetch_bse_filings",
            return_value=rows,
        ):
            result = fetch_filings_in(normalized, lookback_days=30)

        assert result.stage == "filings"
        assert result.status in ("ok", "partial")
        assert len(result.data.get("filings") or []) == 1


@pytest.mark.unit
class TestMacroIn:
    def test_yfinance_vix_and_nifty(self):
        from trade_integrations.dataflows.company_research.sources.resilience import SourceAttempt

        def mock_run_sources(fetchers):
            return [
                SourceAttempt(
                    name="yfinance_vix",
                    status="ok",
                    data={"india_vix": 14.2, "source": "yfinance"},
                ),
                SourceAttempt(
                    name="yfinance_nifty",
                    status="ok",
                    data={
                        "nifty_level": 24500.0,
                        "nifty_change_pct": 0.35,
                        "source": "yfinance",
                    },
                ),
            ]

        with patch(
            "trade_integrations.dataflows.company_research.sources.macro_in.run_sources",
            side_effect=mock_run_sources,
        ):
            result = fetch_macro_in()

        assert result.stage == "macro"
        assert result.status == "ok"
        assert result.data.get("india_vix") == 14.2
        assert result.data.get("nifty_level") == 24500.0
