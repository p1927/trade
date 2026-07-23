"""Tests for TradingConnectorPort and OpenAlgo adapter."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from trade_integrations.execution.adapters.openalgo_adapter import OpenAlgoConnectorAdapter
from trade_integrations.execution.connector_context import ConnectorExecutionContext
from trade_integrations.execution.trading_port import adapter_for_agent
from trade_integrations.openalgo.market_context import MarketContext


def _sample_market_context() -> MarketContext:
    return MarketContext.from_api_data(
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


@pytest.mark.unit
def test_openalgo_adapter_market_context() -> None:
    mock_client = MagicMock()
    mock_client.get_market_context.return_value = _sample_market_context()
    adapter = OpenAlgoConnectorAdapter(client=mock_client)

    ctx = adapter.market_context()

    assert ctx.market_region == "IN"
    assert ctx.analyze_mode is True
    mock_client.get_market_context.assert_called_once_with()


@pytest.mark.unit
def test_openalgo_adapter_quote(monkeypatch) -> None:
    quote_mock = MagicMock(return_value={"ltp": 24500.0, "source": "openalgo"})
    monkeypatch.setattr(
        "trade_integrations.openalgo.market_data.fetch_quote_raw",
        quote_mock,
    )
    adapter = OpenAlgoConnectorAdapter(client=MagicMock())

    result = adapter.quote("NIFTY", exchange="NSE_INDEX")

    assert result == {"ltp": 24500.0, "source": "openalgo"}
    quote_mock.assert_called_once_with("NIFTY", exchange="NSE_INDEX")


@pytest.mark.unit
def test_openalgo_adapter_quotes_batch(monkeypatch) -> None:
    fetch_mock = MagicMock(return_value={"data": [{"symbol": "NIFTY", "exchange": "NSE_INDEX", "ltp": 1.0}]})
    parse_mock = MagicMock(return_value={"NIFTY@NSE_INDEX": {"symbol": "NIFTY", "ltp": 1.0}})
    monkeypatch.setattr(
        "trade_integrations.openalgo.market_data.fetch_multi_quotes_raw",
        fetch_mock,
    )
    monkeypatch.setattr(
        "trade_integrations.openalgo.market_data.parse_multi_quotes_payload",
        parse_mock,
    )
    adapter = OpenAlgoConnectorAdapter(client=MagicMock())
    requests = [{"symbol": "NIFTY", "exchange": "NSE_INDEX"}]

    result = adapter.quotes_batch(requests)

    assert result == {"NIFTY@NSE_INDEX": {"symbol": "NIFTY", "ltp": 1.0}}
    fetch_mock.assert_called_once_with(requests)
    parse_mock.assert_called_once_with(fetch_mock.return_value)


@pytest.mark.unit
def test_openalgo_adapter_positionbook() -> None:
    mock_client = MagicMock()
    mock_client.get_position_book.return_value = [{"symbol": "NIFTY", "qty": 1}]
    adapter = OpenAlgoConnectorAdapter(client=mock_client)

    rows = adapter.positionbook()

    assert rows == [{"symbol": "NIFTY", "qty": 1}]
    mock_client.get_position_book.assert_called_once_with()


@pytest.mark.unit
def test_openalgo_adapter_place_basket() -> None:
    mock_client = MagicMock()
    mock_client.place_basket.return_value = [{"orderid": "123", "status": "success"}]
    adapter = OpenAlgoConnectorAdapter(client=mock_client)
    legs = [{"symbol": "NIFTY", "action": "BUY", "quantity": 1}]

    result = adapter.place_basket(legs, strategy="test_strategy")

    assert result == {"status": "success", "results": [{"orderid": "123", "status": "success"}]}
    mock_client.place_basket.assert_called_once_with(legs, strategy="test_strategy")


@pytest.mark.unit
def test_adapter_for_agent_routes_in_to_openalgo(monkeypatch) -> None:
    ctx = ConnectorExecutionContext(
        profile_id="openalgo-paper-sdk",
        connector="openalgo",
        market="IN",
        backend="openalgo",
        execution_path="openalgo",
        source="agent_stored",
    )
    monkeypatch.setattr(
        "trade_integrations.execution.connector_context.load_active_connector_context",
        lambda *, agent=None: ctx,
    )

    adapter = adapter_for_agent({"connector_profile_id": "openalgo-paper-sdk"})

    assert isinstance(adapter, OpenAlgoConnectorAdapter)


@pytest.mark.unit
def test_adapter_for_agent_alpaca_sdk_routes_to_openalgo(monkeypatch) -> None:
    ctx = ConnectorExecutionContext(
        profile_id="alpaca-paper-sdk",
        connector="alpaca",
        market="US",
        backend="alpaca",
        execution_path="alpaca_sdk",
        source="agent_stored",
    )
    monkeypatch.setattr(
        "trade_integrations.execution.connector_context.load_active_connector_context",
        lambda *, agent=None: ctx,
    )

    adapter = adapter_for_agent({"connector_profile_id": "alpaca-paper-sdk"})

    assert isinstance(adapter, OpenAlgoConnectorAdapter)


@pytest.mark.unit
def test_adapter_for_agent_defaults_to_openalgo_when_no_context(monkeypatch) -> None:
    monkeypatch.setattr(
        "trade_integrations.execution.connector_context.load_active_connector_context",
        lambda *, agent=None: None,
    )

    adapter = adapter_for_agent({})

    assert isinstance(adapter, OpenAlgoConnectorAdapter)
