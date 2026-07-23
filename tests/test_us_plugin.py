"""Tests for US execution routing via OpenAlgo Alpaca plugin."""

from __future__ import annotations

import pytest

from trade_integrations.execution.us_plugin import (
    openalgo_us_via_plugin,
    us_execution_via_openalgo,
)


@pytest.mark.unit
def test_openalgo_us_via_plugin_always_on() -> None:
    assert openalgo_us_via_plugin() is True


@pytest.mark.unit
def test_us_execution_via_openalgo_requires_us_agent() -> None:
    us_agent = {"execution_market": "US", "symbols": ["SPY"]}
    in_agent = {"execution_market": "IN", "symbols": ["NIFTY"]}
    assert us_execution_via_openalgo(us_agent) is True
    assert us_execution_via_openalgo(in_agent) is False
    assert us_execution_via_openalgo(None) is True
