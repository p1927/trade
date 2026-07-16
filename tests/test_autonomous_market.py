"""Tests for autonomous agent execution market routing."""

from __future__ import annotations

from trade_integrations.autonomous_agents.market import (
    agent_execution_market,
    is_us_agent,
    symbol_execution_market,
)


def test_symbol_execution_market() -> None:
    assert symbol_execution_market("NIFTY") == "IN"
    assert symbol_execution_market("SPY") == "US"


def test_agent_execution_market_from_symbols() -> None:
    assert agent_execution_market({"symbols": ["SPY"]}) == "US"
    assert agent_execution_market({"symbols": ["NIFTY"]}) == "IN"


def test_is_us_agent() -> None:
    assert is_us_agent({"execution_market": "US", "symbols": ["SPY"]})
    assert not is_us_agent({"execution_market": "IN", "symbols": ["NIFTY"]})
