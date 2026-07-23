"""Tests for Nautilus bridge market session gates."""

from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

ROOT = Path(__file__).resolve().parents[1]
INTEGRATIONS = ROOT / "integrations"
if str(INTEGRATIONS) not in sys.path:
    sys.path.insert(0, str(INTEGRATIONS))

from nautilus_openalgo_bridge.market_hours import (  # noqa: E402
    any_trading_market_open,
    is_exit_window_open_for_agent,
    is_in_market_session_open,
    is_market_open_for_market,
    is_us_exit_window_open,
    is_us_market_session_open,
)


def test_us_market_closed_on_weekend():
    saturday = datetime(2026, 7, 18, 12, 0, tzinfo=ZoneInfo("America/New_York"))
    assert is_us_market_session_open(now=saturday) is False


def test_us_market_open_midday():
    tuesday = datetime(2026, 7, 14, 11, 0, tzinfo=ZoneInfo("America/New_York"))
    assert is_us_market_session_open(now=tuesday) is True


def test_in_market_closed_overnight_ist(monkeypatch):
    monkeypatch.setattr(
        "trade_integrations.stock_simulator.integration.sim_market_session_open",
        lambda **k: False,
    )
    early = datetime(2026, 7, 14, 5, 0, tzinfo=ZoneInfo("Asia/Kolkata"))
    assert is_in_market_session_open(now=early) is False


def test_market_open_for_market_routes_us(monkeypatch):
    monkeypatch.setattr(
        "trade_integrations.stock_simulator.integration.sim_market_session_open",
        lambda **k: False,
    )
    tuesday_us = datetime(2026, 7, 14, 11, 0, tzinfo=ZoneInfo("America/New_York"))
    assert is_market_open_for_market("US", now=tuesday_us) is True
    assert is_market_open_for_market("IN", now=tuesday_us) is False


def test_any_trading_market_open_when_us_rth_india_closed(monkeypatch):
    monkeypatch.setattr(
        "trade_integrations.stock_simulator.integration.sim_market_session_open",
        lambda **k: False,
    )
    tuesday_us = datetime(2026, 7, 14, 11, 0, tzinfo=ZoneInfo("America/New_York"))
    assert is_in_market_session_open(now=tuesday_us) is False
    assert is_us_market_session_open(now=tuesday_us) is True
    assert any_trading_market_open(now=tuesday_us) is True


def test_us_exit_window_includes_grace_after_close():
    after_close = datetime(2026, 7, 14, 16, 10, tzinfo=ZoneInfo("America/New_York"))
    assert is_us_market_session_open(now=after_close) is False
    assert is_us_exit_window_open(now=after_close) is True


def test_is_exit_window_open_for_agent_us(monkeypatch):
    monkeypatch.setattr(
        "nautilus_openalgo_bridge.market_hours.agent_market",
        lambda _aid: "US",
    )
    open_us = datetime(2026, 7, 14, 11, 0, tzinfo=ZoneInfo("America/New_York"))
    assert is_exit_window_open_for_agent("aa_us", now=open_us) is True
