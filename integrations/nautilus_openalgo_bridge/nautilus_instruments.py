"""Nautilus InstrumentId / IndexInstrument mapping for India watch symbols."""

from __future__ import annotations

from datetime import datetime, timezone

from nautilus_trader.model.currencies import INR
from nautilus_trader.model.data import QuoteTick
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.instruments import IndexInstrument
from nautilus_trader.model.objects import Price, Quantity

from nautilus_openalgo_bridge.instruments import normalize_watch_symbol
from nautilus_openalgo_bridge.models import QuoteSnapshot

VENUE_NSE = "NSE"
PRICE_PRECISION = 2


def watch_symbol_to_instrument_id(symbol: str) -> InstrumentId:
    key = normalize_watch_symbol(symbol)
    return InstrumentId.from_str(f"{key}.{VENUE_NSE}")


def build_index_instrument(symbol: str) -> IndexInstrument:
    key = normalize_watch_symbol(symbol)
    instrument_id = watch_symbol_to_instrument_id(key)
    now_ns = int(datetime.now(timezone.utc).timestamp() * 1_000_000_000)
    return IndexInstrument(
        instrument_id=instrument_id,
        raw_symbol=instrument_id.symbol,
        currency=INR,
        price_precision=PRICE_PRECISION,
        size_precision=0,
        price_increment=Price.from_str("0.01"),
        size_increment=Quantity.from_int(1),
        ts_event=now_ns,
        ts_init=now_ns,
    )


def quote_snapshot_to_tick(
    snap: QuoteSnapshot,
    *,
    instrument_id: InstrumentId | None = None,
) -> QuoteTick:
    iid = instrument_id or watch_symbol_to_instrument_id(snap.symbol)
    price = Price.from_str(f"{snap.ltp:.{PRICE_PRECISION}f}")
    qty = Quantity.from_int(1)
    try:
        from nautilus_trader.core.datetime import dt_to_unix_nanos

        ts = dt_to_unix_nanos(datetime.fromisoformat(snap.fetched_at.replace("Z", "+00:00")))
    except (ValueError, TypeError):
        ts = int(datetime.now(timezone.utc).timestamp() * 1_000_000_000)
    return QuoteTick(
        instrument_id=iid,
        bid_price=price,
        ask_price=price,
        bid_size=qty,
        ask_size=qty,
        ts_event=ts,
        ts_init=ts,
    )


def default_watch_instrument_ids(symbols: tuple[str, ...]) -> list[InstrumentId]:
    return [watch_symbol_to_instrument_id(symbol) for symbol in symbols]
