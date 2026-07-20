"""Nautilus watch feed polls OpenAlgo multiquotes via fetch_multi_quotes_raw."""

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


def test_poll_uses_fetch_multi_quotes_raw(hub_tmp, monkeypatch):
    from nautilus_openalgo_bridge.config import BridgeConfig
    from nautilus_openalgo_bridge.data_feed import OpenAlgoQuoteFeed

    client_calls = {"multi": 0}

    class FakeClient:
        def get_multi_quotes(self, symbols):
            client_calls["multi"] += 1
            return {}

        def get_quote(self, symbol, *, exchange="NSE"):
            raise AssertionError("fallback should not run when multiquotes returns data")

    raw_calls = {"n": 0, "requests": None}

    def fake_fetch_multi_quotes_raw(requests):
        raw_calls["n"] += 1
        raw_calls["requests"] = requests
        return {
            "quotes": [
                {
                    "symbol": "NIFTY",
                    "exchange": "NSE_INDEX",
                    "ltp": 24500.0,
                }
            ]
        }

    monkeypatch.setattr(
        "nautilus_openalgo_bridge.data_feed.fetch_multi_quotes_raw",
        fake_fetch_multi_quotes_raw,
    )
    monkeypatch.setattr(
        "trade_integrations.openalgo.ws_client.ensure_ws_feed",
        lambda _requests: None,
    )

    feed = OpenAlgoQuoteFeed(
        client=FakeClient(),
        config=BridgeConfig(openalgo_host="http://127.0.0.1:5001", openalgo_api_key="test"),
    )
    quotes = feed.poll(["NIFTY"])

    assert raw_calls["n"] == 1
    assert raw_calls["requests"] == [{"symbol": "NIFTY", "exchange": "NSE_INDEX"}]
    assert client_calls["multi"] == 0
    assert "NIFTY" in quotes
    assert quotes["NIFTY"].ltp == 24500.0
