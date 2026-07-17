"""Tests for Nautilus bridge instrument mapping."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INTEGRATIONS = ROOT / "integrations"
if str(INTEGRATIONS) not in sys.path:
    sys.path.insert(0, str(INTEGRATIONS))

from nautilus_openalgo_bridge.instruments import position_rows_to_legs  # noqa: E402


def test_position_rows_to_legs_filters_by_underlying():
    rows = [
        {
            "symbol": "NIFTY24JUL24500CE",
            "exchange": "NFO",
            "quantity": 50,
            "product": "NRML",
        },
        {
            "symbol": "BANKNIFTY24JUL52000CE",
            "exchange": "NFO",
            "quantity": 25,
            "product": "NRML",
        },
    ]
    legs = position_rows_to_legs(rows, underlying="NIFTY")
    assert len(legs) == 1
    assert legs[0].symbol == "NIFTY24JUL24500CE"
    assert legs[0].quantity == 50


def test_position_rows_to_legs_keeps_cash_symbol_for_underlying():
    rows = [
        {"symbol": "RELIANCE", "exchange": "NSE", "quantity": 10, "product": "CNC"},
        {"symbol": "TCS", "exchange": "NSE", "quantity": 5, "product": "CNC"},
    ]
    legs = position_rows_to_legs(rows, underlying="RELIANCE")
    assert len(legs) == 1
    assert legs[0].symbol == "RELIANCE"
