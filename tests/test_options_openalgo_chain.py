"""OpenAlgo option chain response normalization."""

from __future__ import annotations

import pytest

from trade_integrations.dataflows.openalgo import (
    fetch_option_chain,
    normalize_openalgo_expiry,
)


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
        chain = fetch_option_chain(
            "NIFTY",
            "NFO",
            expiry_date="21JUL26",
            strike_count=5,
        )
        assert len(chain.get("chain") or []) > 0
        assert chain.get("underlying_ltp") is not None
