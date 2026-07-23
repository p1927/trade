"""Tests for OpenAlgo MarketContext client."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from trade_integrations.openalgo.market_context import (
    MarketContext,
    MarketContextError,
    fetch_market_context,
)


@pytest.mark.unit
def test_market_context_from_api_data() -> None:
    ctx = MarketContext.from_api_data(
        {
            "context_generation": "2026-07-23T09:15:00+05:30",
            "data_broker": "zerodha",
            "execution_venue": "sandbox",
            "analyze_mode": True,
            "market_region": "IN",
            "positions_authority": "sandbox.db",
            "quotes_source": "broker_plugin",
            "simulator": {"active": False},
            "capabilities": ["options", "equity"],
        }
    )
    assert ctx.data_broker == "zerodha"
    assert ctx.execution_venue == "sandbox"
    assert ctx.capabilities == ("options", "equity")


@pytest.mark.unit
def test_market_context_rejects_partial_payload() -> None:
    with pytest.raises(MarketContextError, match="missing fields"):
        MarketContext.from_api_data({"data_broker": "zerodha"})


@pytest.mark.unit
def test_fetch_market_context_success(monkeypatch) -> None:
    mock_client = MagicMock()
    mock_client.post.return_value = {
        "status": "success",
        "data": {
            "context_generation": "2026-07-23T09:15:00+05:30",
            "data_broker": "zerodha",
            "execution_venue": "broker",
            "analyze_mode": False,
            "market_region": "IN",
            "positions_authority": "broker",
            "quotes_source": "broker_plugin",
            "simulator": {"active": False},
            "capabilities": ["equity"],
        },
    }
    monkeypatch.setattr(
        "trade_integrations.openalgo.rest_client.OpenAlgoRestClient",
        lambda **kwargs: mock_client,
    )
    ctx = fetch_market_context(host="http://127.0.0.1:5001", api_key="test-key")
    assert ctx.analyze_mode is False
    assert ctx.positions_authority == "broker"
    mock_client.post.assert_called_once_with(
        "marketcontext",
        {"apikey": "test-key"},
        timeout=20,
    )


@pytest.mark.unit
def test_fetch_market_context_error_status(monkeypatch) -> None:
    mock_client = MagicMock()
    mock_client.post.return_value = {"status": "error", "message": "Invalid openalgo apikey"}
    monkeypatch.setattr(
        "trade_integrations.openalgo.rest_client.OpenAlgoRestClient",
        lambda **kwargs: mock_client,
    )
    with pytest.raises(MarketContextError, match="Invalid openalgo apikey"):
        fetch_market_context(host="http://127.0.0.1:5001", api_key="bad")
