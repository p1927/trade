"""Nautilus watch feed routes multiquote polls through hub channel."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
INTEGRATIONS = ROOT / "integrations"
if str(INTEGRATIONS) not in sys.path:
    sys.path.insert(0, str(INTEGRATIONS))


@pytest.fixture
def hub_tmp(tmp_path, monkeypatch):
    hub = tmp_path / "hub"
    hub.mkdir()
    monkeypatch.setenv("TRADE_STACK_HUB_DIR", str(hub))
    return hub


def test_poll_uses_hub_channel_not_client_get_multi_quotes(hub_tmp, monkeypatch):
    from trade_integrations.openalgo.freshness import FreshnessPolicy
    from trade_integrations.openalgo.market_data import fetch_multi_quotes_raw

    from nautilus_openalgo_bridge.config import BridgeConfig
    from nautilus_openalgo_bridge.data_feed import OpenAlgoQuoteFeed

    client_calls = {"multi": 0}

    class FakeClient:
        def get_multi_quotes(self, symbols):
            client_calls["multi"] += 1
            return {}

        def get_quote(self, symbol, *, exchange="NSE"):
            raise AssertionError("fallback should not run when channel returns quotes")

    channel_calls = {"n": 0, "policy": None, "fetch_fn": None, "requests": None}

    def fake_get_multi_quotes(requests, fetch_fn, *, policy):
        channel_calls["n"] += 1
        channel_calls["policy"] = policy
        channel_calls["fetch_fn"] = fetch_fn
        channel_calls["requests"] = requests
        return {
            "NIFTY@NSE_INDEX": {
                "symbol": "NIFTY",
                "exchange": "NSE_INDEX",
                "ltp": 24500.0,
            }
        }

    monkeypatch.setattr(
        "nautilus_openalgo_bridge.data_feed.get_multi_quotes",
        fake_get_multi_quotes,
    )

    feed = OpenAlgoQuoteFeed(
        client=FakeClient(),
        config=BridgeConfig(openalgo_host="http://127.0.0.1:5001", openalgo_api_key="test"),
    )
    quotes = feed.poll(["NIFTY"])

    assert channel_calls["n"] == 1
    assert channel_calls["policy"] is FreshnessPolicy.WATCH
    assert channel_calls["fetch_fn"] is fetch_multi_quotes_raw
    assert channel_calls["requests"] == [{"symbol": "NIFTY", "exchange": "NSE_INDEX"}]
    assert client_calls["multi"] == 0
    assert "NIFTY" in quotes
    assert quotes["NIFTY"].ltp == 24500.0
