"""Tests for OpenAlgo WebSocket L1 seeding."""

from __future__ import annotations


def test_seed_quote_l1_populates_watch_cache(monkeypatch):
    from trade_integrations.hub_capture.channel import seed_quote_l1
    from trade_integrations.openalgo.freshness import FreshnessPolicy, ttl_seconds

    monkeypatch.setenv("OPENALGO_WATCH_QUOTE_TTL_SECONDS", "5")
    seed_quote_l1("NIFTY", "NSE_INDEX", {"ltp": 24500.0, "source": "openalgo_ws"})

    from trade_integrations.hub_capture import channel as ch

    l1_key = ch._quote_l1_key("NIFTY", "NSE_INDEX")
    cached = ch._l1_cache.get(l1_key)
    assert cached is not None
    assert cached["ltp"] == 24500.0
    assert ttl_seconds(FreshnessPolicy.WATCH) == 5
