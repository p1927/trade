"""OpenAlgo option chain response normalization."""

from __future__ import annotations

import pytest

from trade_integrations.openalgo.market_data import fetch_option_chain
from trade_integrations.openalgo.symbols import normalize_openalgo_expiry


@pytest.mark.unit
class TestOpenAlgoOptionChain:
    def test_normalize_expiry(self):
        assert normalize_openalgo_expiry("21-JUL-26") == "21JUL26"
        assert normalize_openalgo_expiry("28JUL26") == "28JUL26"

    def test_fetch_chain_live(self, monkeypatch):
        """Integration smoke when OpenAlgo is reachable."""
        import os

        if not os.getenv("OPENALGO_API_KEY"):
            pytest.skip("OPENALGO_API_KEY not set")
        try:
            from trade_integrations.openalgo.rest_client import openalgo_settings

            host, _ = openalgo_settings()
            import urllib.request

            urllib.request.urlopen(f"{host.rstrip('/')}/health", timeout=2)
        except Exception:
            pytest.skip("OpenAlgo not reachable")
        from trade_integrations.dataflows.errors import NoMarketDataError
        from trade_integrations.openalgo.market_data import fetch_option_expiry_dates

        expiries = fetch_option_expiry_dates("NIFTY", "NFO")
        if not expiries:
            pytest.skip("No NIFTY option expiries from OpenAlgo")
        try:
            chain = fetch_option_chain(
                "NIFTY",
                "NFO",
                expiry_date=expiries[0],
                strike_count=5,
            )
        except NoMarketDataError:
            pytest.skip("OpenAlgo option chain unavailable for current expiry")
        assert len(chain.get("chain") or []) > 0
        assert chain.get("underlying_ltp") is not None
