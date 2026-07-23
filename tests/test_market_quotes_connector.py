"""Tests for connector-aware market_quotes routing."""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from trade_integrations.dataflows.market_quotes import fetch_live_quote, resolve_quote_market
from trade_integrations.execution.connector_context import ConnectorExecutionContext, load_active_connector_context


@pytest.mark.unit
def test_resolve_quote_market_prefers_connector_over_symbol() -> None:
    ctx = ConnectorExecutionContext(
        profile_id="alpaca-paper-sdk",
        connector="alpaca",
        market="US",
        backend="alpaca",
        execution_path="alpaca_sdk",
        source="selected_profile",
    )
    assert resolve_quote_market("NIFTY", ctx) == "US"
    ctx_in = ConnectorExecutionContext(
        profile_id="openalgo-paper-sdk",
        connector="openalgo",
        market="IN",
        backend="openalgo",
        execution_path="openalgo",
        source="selected_profile",
    )
    assert resolve_quote_market("AAPL", ctx_in) == "IN"


@pytest.mark.unit
def test_fetch_live_quote_with_agent_uses_adapter(monkeypatch) -> None:
    adapter_mock = MagicMock()
    adapter_mock.quote.return_value = {"symbol": "NIFTY", "ltp": 24500.0}
    adapter_for_agent_mock = MagicMock(return_value=adapter_mock)
    monkeypatch.setattr(
        "trade_integrations.execution.trading_port.adapter_for_agent",
        adapter_for_agent_mock,
    )
    openalgo_mock = MagicMock(return_value={"symbol": "NIFTY", "ltp": 99.0})
    monkeypatch.setattr(
        "trade_integrations.dataflows.openalgo.fetch_openalgo_quote",
        openalgo_mock,
    )

    agent = {"connector_profile_id": "openalgo-paper-sdk"}
    result = fetch_live_quote("NIFTY", agent=agent)

    assert result == {"symbol": "NIFTY", "ltp": 24500.0}
    adapter_for_agent_mock.assert_called_once_with(agent)
    adapter_mock.quote.assert_called_once_with("NIFTY")
    openalgo_mock.assert_not_called()


@pytest.mark.unit
def test_fetch_live_quote_agent_adapter_failure_falls_back(monkeypatch) -> None:
    def _raise(_agent: dict) -> MagicMock:
        raise NotImplementedError("connector_sdk")

    monkeypatch.setattr(
        "trade_integrations.execution.trading_port.adapter_for_agent",
        _raise,
    )
    openalgo_mock = MagicMock(return_value={"symbol": "NIFTY", "ltp": 24000.0})
    monkeypatch.setattr(
        "trade_integrations.dataflows.openalgo.fetch_openalgo_quote",
        openalgo_mock,
    )
    ctx = ConnectorExecutionContext(
        profile_id="openalgo-paper-sdk",
        connector="openalgo",
        market="IN",
        backend="openalgo",
        execution_path="openalgo",
        source="selected_profile",
    )
    agent = {"connector_profile_id": "openalgo-paper-sdk", "execution_market": "IN"}
    result = fetch_live_quote("NIFTY", agent=agent, connector_context=ctx)
    assert result == {"symbol": "NIFTY", "ltp": 24000.0}
    openalgo_mock.assert_called_once_with("NIFTY")


@pytest.mark.unit
def test_fetch_live_quote_us_connector_calls_openalgo(tmp_path, monkeypatch) -> None:
    runtime = tmp_path / "vibe-trading"
    runtime.mkdir()
    (runtime / "trading-connections.json").write_text(
        json.dumps({"selected_profile": "alpaca-paper-sdk"}),
        encoding="utf-8",
    )
    monkeypatch.setenv("VIBE_TRADING_RUNTIME_ROOT", str(runtime))

    openalgo_mock = MagicMock(return_value={"symbol": "AAPL", "ltp": 99.0})
    monkeypatch.setattr(
        "trade_integrations.dataflows.openalgo.fetch_openalgo_quote",
        openalgo_mock,
    )

    result = fetch_live_quote("AAPL")
    assert result == {"symbol": "AAPL", "ltp": 99.0}
    openalgo_mock.assert_called_once_with("AAPL")


@pytest.mark.unit
def test_fetch_live_quote_us_connector_uses_openalgo_when_plugin_flag(tmp_path, monkeypatch) -> None:
    runtime = tmp_path / "vibe-trading"
    runtime.mkdir()
    (runtime / "trading-connections.json").write_text(
        json.dumps({"selected_profile": "alpaca-paper-sdk"}),
        encoding="utf-8",
    )
    monkeypatch.setenv("VIBE_TRADING_RUNTIME_ROOT", str(runtime))

    alpaca_mock = MagicMock(return_value={"symbol": "AAPL", "ltp": 150.0})
    monkeypatch.setattr(
        "trade_integrations.dataflows.alpaca.fetch_alpaca_quote",
        alpaca_mock,
    )
    monkeypatch.setattr(
        "trade_integrations.dataflows.alpaca.alpaca_configured",
        lambda: True,
    )
    openalgo_mock = MagicMock(return_value={"symbol": "AAPL", "ltp": 99.0})
    monkeypatch.setattr(
        "trade_integrations.dataflows.openalgo.fetch_openalgo_quote",
        openalgo_mock,
    )

    result = fetch_live_quote("AAPL")
    assert result == {"symbol": "AAPL", "ltp": 99.0}
    openalgo_mock.assert_called_once_with("AAPL")
    alpaca_mock.assert_not_called()


@pytest.mark.unit
def test_fetch_live_quote_in_connector_skips_alpaca_for_us_symbol(tmp_path, monkeypatch) -> None:
    runtime = tmp_path / "vibe-trading"
    runtime.mkdir()
    (runtime / "trading-connections.json").write_text(
        json.dumps({"selected_profile": "openalgo-paper-sdk"}),
        encoding="utf-8",
    )
    monkeypatch.setenv("VIBE_TRADING_RUNTIME_ROOT", str(runtime))

    alpaca_mock = MagicMock(return_value={"symbol": "AAPL", "ltp": 150.0})
    monkeypatch.setattr(
        "trade_integrations.dataflows.alpaca.fetch_alpaca_quote",
        alpaca_mock,
    )
    monkeypatch.setattr(
        "trade_integrations.dataflows.alpaca.alpaca_configured",
        lambda: True,
    )
    openalgo_mock = MagicMock(return_value={"symbol": "AAPL", "ltp": 99.0})
    monkeypatch.setattr(
        "trade_integrations.dataflows.openalgo.fetch_openalgo_quote",
        openalgo_mock,
    )

    result = fetch_live_quote("AAPL")
    assert result == {"symbol": "AAPL", "ltp": 99.0}
    openalgo_mock.assert_called_once_with("AAPL")
    alpaca_mock.assert_not_called()


@pytest.mark.unit
def test_load_active_connector_context_from_runtime(tmp_path, monkeypatch) -> None:
    runtime = tmp_path / "vibe-trading"
    runtime.mkdir()
    (runtime / "trading-connections.json").write_text(
        json.dumps({"selected_profile": "alpaca-paper-sdk"}),
        encoding="utf-8",
    )
    monkeypatch.setenv("VIBE_TRADING_RUNTIME_ROOT", str(runtime))
    ctx = load_active_connector_context()
    assert ctx is not None
    assert ctx.market == "US"
