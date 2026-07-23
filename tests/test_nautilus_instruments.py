"""Tests for Nautilus bridge instrument mapping."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
INTEGRATIONS = ROOT / "integrations"
if str(INTEGRATIONS) not in sys.path:
    sys.path.insert(0, str(INTEGRATIONS))

from nautilus_openalgo_bridge.instruments import (  # noqa: E402
    multiquote_requests,
    position_rows_to_legs,
    resolve_openalgo_symbol,
)


def test_multiquote_requests_us_symbol_uses_nasdaq() -> None:
    rows = multiquote_requests(["SPY"])
    assert rows == [{"symbol": "SPY", "exchange": "NASDAQ"}]


def test_resolve_openalgo_symbol_us_equity() -> None:
    assert resolve_openalgo_symbol("AAPL") == ("AAPL", "NASDAQ")


def test_instrument_id_for_watch_symbol_us_routes_nasdaq() -> None:
    pytest.importorskip("nautilus_trader")
    from nautilus_openalgo_bridge.nautilus_instruments import instrument_id_for_watch_symbol

    iid = instrument_id_for_watch_symbol("SPY")
    assert str(iid) == "SPY.NASDAQ"


def test_quote_snapshot_to_tick_us_without_instrument_id() -> None:
    pytest.importorskip("nautilus_trader")
    from nautilus_openalgo_bridge.models import QuoteSnapshot
    from nautilus_openalgo_bridge.nautilus_instruments import quote_snapshot_to_tick

    snap = QuoteSnapshot(symbol="SPY", ltp=450.1234, fetched_at="2026-07-14T15:00:00+00:00")
    tick = quote_snapshot_to_tick(snap)
    assert str(tick.instrument_id) == "SPY.NASDAQ"
    assert str(tick.bid_price) == "450.1234"


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


def test_quote_snapshot_to_tick_us_uses_four_decimal_precision() -> None:
    pytest.importorskip("nautilus_trader")
    from nautilus_openalgo_bridge.models import QuoteSnapshot
    from nautilus_openalgo_bridge.nautilus_instruments import (
        quote_snapshot_to_tick,
        us_symbol_to_instrument_id,
    )

    snap = QuoteSnapshot(symbol="SPY", ltp=450.1234, fetched_at="2026-07-14T15:00:00+00:00")
    tick = quote_snapshot_to_tick(snap, instrument_id=us_symbol_to_instrument_id("SPY"))
    assert str(tick.bid_price) == "450.1234"
