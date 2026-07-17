"""Tests for Nautilus bridge config helpers."""

from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
INTEGRATIONS = ROOT / "integrations"
if str(INTEGRATIONS) not in sys.path:
    sys.path.insert(0, str(INTEGRATIONS))

from nautilus_openalgo_bridge.config import (  # noqa: E402
    BridgeConfig,
    _parse_hhmm,
    is_bridge_market_open,
)


def test_parse_hhmm_invalid_returns_default():
    assert _parse_hhmm("bad") == (9, 20)
    assert _parse_hhmm("aa:bb") == (9, 20)


def test_parse_hhmm_valid():
    assert _parse_hhmm("09:20") == (9, 20)
    assert _parse_hhmm(" 15:10 ") == (15, 10)


def test_is_bridge_market_open_tolerates_corrupt_hhmm():
    cfg = BridgeConfig(market_open="aa:bb", market_close="cc:dd")
    at_default = datetime(2026, 7, 14, 9, 20, tzinfo=ZoneInfo("Asia/Kolkata"))
    assert is_bridge_market_open(cfg, now=at_default) is True
