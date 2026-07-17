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
    is_in_market_session_open,
    is_market_open_for_market,
    is_us_market_session_open,
)


def test_us_market_closed_on_weekend():
    saturday = datetime(2026, 7, 18, 12, 0, tzinfo=ZoneInfo("America/New_York"))
    assert is_us_market_session_open(now=saturday) is False


def test_us_market_open_midday():
    tuesday = datetime(2026, 7, 14, 11, 0, tzinfo=ZoneInfo("America/New_York"))
    assert is_us_market_session_open(now=tuesday) is True


def test_in_market_closed_overnight_ist():
    early = datetime(2026, 7, 14, 5, 0, tzinfo=ZoneInfo("Asia/Kolkata"))
    assert is_in_market_session_open(now=early) is False


def test_market_open_for_market_routes_us():
    tuesday_us = datetime(2026, 7, 14, 11, 0, tzinfo=ZoneInfo("America/New_York"))
    assert is_market_open_for_market("US", now=tuesday_us) is True
    assert is_market_open_for_market("IN", now=tuesday_us) is False
