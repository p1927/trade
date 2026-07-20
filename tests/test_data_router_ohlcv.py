"""Tests for DataRouter OHLCV sequential fetch."""

from __future__ import annotations

from unittest.mock import patch

import pandas as pd
import pytest

from trade_integrations.data_router import FetchSpec, fetch
from trade_integrations.data_router import normalized_store as ns
from trade_integrations.tiered_api.cache_policy import should_cache_response


@pytest.fixture
def hub_tmp(monkeypatch, tmp_path):
    monkeypatch.setenv("TRADE_STACK_HUB_DIR", str(tmp_path))
    monkeypatch.setenv("DATA_ROUTER_ENABLED", "1")
    yield tmp_path


@pytest.mark.unit
def test_normalized_store_read_write(hub_tmp):
    spec = FetchSpec(
        domain="ohlcv",
        market="india_equity",
        symbol="RELIANCE",
        start="2024-01-01",
        end="2024-01-31",
    )
    frame = pd.DataFrame(
        {
            "date": ["2024-01-02", "2024-01-03"],
            "open": [1.0, 2.0],
            "high": [1.1, 2.1],
            "low": [0.9, 1.9],
            "close": [1.05, 2.05],
            "volume": [100, 200],
        }
    )
    path = ns.write_ohlcv(spec, frame, source="test")
    assert path is not None
    data, read_path, hit = ns.read(spec)
    assert hit is True
    assert len(data) == 2


@pytest.mark.unit
def test_cache_policy_rejects_error_envelope():
    assert should_cache_response({"Note": "Thank you for using Alpha Vantage! Our standard API rate limit is 25 requests per day."}) is False
    assert should_cache_response({"date": "2024-01-01", "close": 1.0}) is True
    assert should_cache_response([]) is False


@pytest.mark.unit
def test_fetch_hub_hit_skips_adapters(hub_tmp):
    spec = FetchSpec(
        domain="ohlcv",
        market="india_equity",
        symbol="TCS",
        start="2024-01-01",
        end="2024-01-10",
    )
    frame = pd.DataFrame(
        {
            "date": ["2024-01-02"],
            "open": [1.0],
            "high": [1.1],
            "low": [0.9],
            "close": [1.0],
            "volume": [10],
        }
    )
    ns.write_ohlcv(spec, frame, source="hub_seed")
    with patch("trade_integrations.data_router.adapters.ohlcv.fetch_ohlcv") as mock_fetch:
        result = fetch(spec)
        mock_fetch.assert_not_called()
    assert result.status == "ok"
    assert result.cache_hit is True


@pytest.mark.unit
def test_fetch_chain_fallback_on_budget(hub_tmp, monkeypatch):
    spec = FetchSpec(
        domain="ohlcv",
        market="india_equity",
        symbol="INFY",
        start="2024-01-01",
        end="2024-01-10",
    )
    calls = []

    def fake_fetch(source_id, call_spec):
        calls.append(source_id)
        if source_id == "openalgo":
            from trade_integrations.data_router.adapters.ohlcv import AdapterError

            raise AdapterError("budget", reason="budget_exhausted")
        if source_id == "yfinance":
            return pd.DataFrame(
                {
                    "date": ["2024-01-02"],
                    "open": [1.0],
                    "high": [1.1],
                    "low": [0.9],
                    "close": [1.0],
                    "volume": [10],
                }
            )
        from trade_integrations.data_router.adapters.ohlcv import AdapterError

        raise AdapterError("no", reason="no_data")

    monkeypatch.setenv(
        "DATA_ROUTER_CHAIN_OHLCV_INDIA_EQUITY",
        "openalgo,yfinance",
    )
    with patch("trade_integrations.data_router.router.fetch_ohlcv", side_effect=fake_fetch):
        result = fetch(spec)
    assert result.status == "ok"
    assert result.source_id == "yfinance"
    assert "openalgo" in calls
    assert "yfinance" in calls
