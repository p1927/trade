"""MCP market-data tools route through hub channel, not pip SDK client."""

from __future__ import annotations

import importlib.util
import json
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def hub_tmp(tmp_path, monkeypatch):
    hub = tmp_path / "hub"
    hub.mkdir()
    monkeypatch.setenv("TRADE_STACK_HUB_DIR", str(hub))
    monkeypatch.setenv("TRADINGAGENTS_OPTIONS_CACHE_MINUTES", "30")
    return hub


def _fake_chain(underlying, exchange, *, expiry_date=None, strike_count=None):
    return {
        "underlying": underlying.upper(),
        "underlying_ltp": 24500.0,
        "expiry_date": expiry_date or "16JUL26",
        "chain": [
            {
                "strike": 24500,
                "ce": {"ltp": 100.0, "oi": 500},
                "pe": {"ltp": 95.0, "oi": 600},
            }
        ],
        "source": "mock_vendor",
    }


def _load_mcp_module(monkeypatch):
    """Load mcpserver with HTTP boot and a stub SDK client."""
    monkeypatch.setenv("OPENALGO_MCP_HTTP_BOOT", "1")
    monkeypatch.setenv("TRADE_INTEGRATIONS_SKIP_APPLY", "1")

    sdk_calls = {"optionchain": 0, "quotes": 0, "multiquotes": 0}

    class _StubClient:
        def optionchain(self, **kwargs):
            sdk_calls["optionchain"] += 1
            return {"status": "success", "data": _fake_chain(kwargs["underlying"], kwargs["exchange"])}

        def quotes(self, **kwargs):
            sdk_calls["quotes"] += 1
            return {"status": "success", "data": {"ltp": 1.0, "symbol": kwargs["symbol"]}}

        def multiquotes(self, **kwargs):
            sdk_calls["multiquotes"] += 1
            return {
                "status": "success",
                "data": {
                    "quotes": [
                        {"symbol": row["symbol"], "exchange": row["exchange"], "ltp": 1.0}
                        for row in kwargs["symbols"]
                    ]
                },
            }

    if "openalgo" not in sys.modules:
        stub = types.ModuleType("openalgo")
        stub.api = lambda **kwargs: _StubClient()
        stub.ta = types.ModuleType("openalgo.ta")
        sys.modules["openalgo"] = stub

    mcpserver_path = ROOT / "openalgo" / "mcp" / "mcpserver.py"
    spec = importlib.util.spec_from_file_location("openalgo_mcpserver_hub_test", mcpserver_path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.init_for_http("test-key", "http://127.0.0.1:5001")
    mod.client = _StubClient()
    mod._sdk_calls = sdk_calls
    return mod


def test_get_option_chain_two_calls_one_vendor_fetch(hub_tmp, monkeypatch):
    from trade_integrations.hub_capture.registry import save_registry, update_entity

    save_registry({"entities": []})
    update_entity("NIFTY", {"capture_enabled": True, "factor_groups": ["derivatives"]})

    vendor_calls = {"n": 0}

    def counting_fetch(underlying, exchange, *, expiry_date=None, strike_count=None):
        vendor_calls["n"] += 1
        return _fake_chain(underlying, exchange, expiry_date=expiry_date, strike_count=strike_count)

    monkeypatch.setattr(
        "trade_integrations.dataflows.openalgo._fetch_option_chain_raw",
        counting_fetch,
    )

    mcp = _load_mcp_module(monkeypatch)

    first = mcp.get_option_chain("NIFTY", "NSE_INDEX", "16JUL26", 10)
    second = mcp.get_option_chain("NIFTY", "NSE_INDEX", "16JUL26", 10)

    assert vendor_calls["n"] == 1
    assert mcp._sdk_calls["optionchain"] == 0

    first_payload = json.loads(first)
    second_payload = json.loads(second)
    assert first_payload["underlying"] == "NIFTY"
    assert second_payload.get("channel") == "hub_latest"


def test_get_quote_routes_through_channel_not_sdk(hub_tmp, monkeypatch):
    from trade_integrations.hub_capture.registry import save_registry, update_entity

    save_registry({"entities": []})
    update_entity("NIFTY", {"capture_enabled": True, "factor_groups": ["derivatives"]})

    vendor_calls = {"n": 0, "exchange": None}

    def counting_quote(symbol, *, exchange=None):
        vendor_calls["n"] += 1
        vendor_calls["exchange"] = exchange
        return {"ltp": 24500.0, "source": "mock_vendor"}

    monkeypatch.setattr(
        "trade_integrations.openalgo.market_data.fetch_quote_raw",
        counting_quote,
    )

    mcp = _load_mcp_module(monkeypatch)
    raw = mcp.get_quote("NIFTY", "NSE_INDEX")
    payload = json.loads(raw)

    assert vendor_calls["n"] == 1
    assert vendor_calls["exchange"] == "NSE_INDEX"
    assert mcp._sdk_calls["quotes"] == 0
    assert payload["ltp"] == 24500.0


def test_get_multi_quotes_routes_through_channel_not_sdk(hub_tmp, monkeypatch):
    vendor_calls = {"n": 0}

    def counting_multi(requests):
        vendor_calls["n"] += 1
        return {
            "quotes": [
                {
                    "symbol": row["symbol"],
                    "exchange": row["exchange"],
                    "ltp": 24500.0,
                    "source": "mock_vendor",
                }
                for row in requests
            ]
        }

    monkeypatch.setattr(
        "trade_integrations.openalgo.market_data.fetch_multi_quotes_raw",
        counting_multi,
    )

    mcp = _load_mcp_module(monkeypatch)
    symbols = [
        {"symbol": "NIFTY", "exchange": "NSE_INDEX"},
        {"symbol": "RELIANCE", "exchange": "NSE"},
    ]
    raw = mcp.get_multi_quotes(symbols)
    payload = json.loads(raw)

    assert vendor_calls["n"] == 1
    assert mcp._sdk_calls["multiquotes"] == 0
    assert payload["NIFTY@NSE_INDEX"]["ltp"] == 24500.0
    assert payload["RELIANCE@NSE"]["ltp"] == 24500.0
