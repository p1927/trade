"""NSE/NFO symbol mapping for OpenAlgo quote requests."""

from __future__ import annotations

import logging
from typing import Any

from nautilus_openalgo_bridge.models import ExecutionLeg

logger = logging.getLogger(__name__)

# symbol -> (OpenAlgo symbol, exchange)
WATCH_SYMBOL_MAP: dict[str, tuple[str, str]] = {
    "NIFTY": ("NIFTY", "NSE_INDEX"),
    "NIFTY50": ("NIFTY", "NSE_INDEX"),
    "BANKNIFTY": ("BANKNIFTY", "NSE_INDEX"),
    "FINNIFTY": ("FINNIFTY", "NSE_INDEX"),
    "MIDCPNIFTY": ("MIDCPNIFTY", "NSE_INDEX"),
    "SENSEX": ("SENSEX", "BSE_INDEX"),
    "INDIAVIX": ("INDIAVIX", "NSE_INDEX"),
    "VIX": ("INDIAVIX", "NSE_INDEX"),
}


def normalize_watch_symbol(symbol: str) -> str:
    return symbol.strip().upper().replace(" ", "")


def resolve_openalgo_symbol(symbol: str) -> tuple[str, str]:
    """Return (symbol, exchange) for OpenAlgo multiquotes."""
    key = normalize_watch_symbol(symbol)
    if key in WATCH_SYMBOL_MAP:
        return WATCH_SYMBOL_MAP[key]
    if key.endswith((".NS", ".BO")):
        base = key.rsplit(".", 1)[0]
        exchange = "NSE" if key.endswith(".NS") else "BSE"
        return base, exchange
    return key, "NSE"


def multiquote_requests(symbols: list[str]) -> list[dict[str, str]]:
    seen: set[tuple[str, str]] = set()
    rows: list[dict[str, str]] = []
    for symbol in symbols:
        oa_symbol, exchange = resolve_openalgo_symbol(symbol)
        key = (oa_symbol, exchange)
        if key in seen:
            continue
        seen.add(key)
        rows.append({"symbol": oa_symbol, "exchange": exchange})
    return rows


def position_rows_to_legs(
    rows: list[dict[str, Any]],
    *,
    underlying: str,
) -> list[ExecutionLeg]:
    """Map OpenAlgo positionbook rows to bridge ExecutionLeg list."""
    legs: list[ExecutionLeg] = []
    ul = underlying.upper()
    for row in rows:
        symbol = str(row.get("symbol") or row.get("tradingsymbol") or "").upper()
        if not symbol:
            continue
        exchange = str(row.get("exchange") or "NFO").upper()
        try:
            qty = int(float(row.get("quantity") or row.get("netqty") or 0))
        except (TypeError, ValueError):
            continue
        if qty == 0:
            continue
        action = "BUY" if qty > 0 else "SELL"
        legs.append(
            ExecutionLeg(
                symbol=symbol,
                exchange=exchange,
                action=action,
                quantity=abs(qty),
                product=str(row.get("product") or "NRML").upper(),
            )
        )
    if not legs and ul:
        logger.debug("no open legs for underlying %s in position book", ul)
    return legs


def validate_option_legs(
    legs: list[ExecutionLeg],
    client: Any,
) -> list[ExecutionLeg]:
    """Verify F&O symbols exist via OpenAlgo symbol endpoint."""
    validated: list[ExecutionLeg] = []
    for leg in legs:
        if leg.exchange not in ("NFO", "BFO", "MCX"):
            validated.append(leg)
            continue
        try:
            info = client.get_symbol_info(leg.symbol, exchange=leg.exchange)
            if info:
                validated.append(leg)
        except RuntimeError:
            logger.warning("symbol info unavailable for %s:%s", leg.symbol, leg.exchange)
            validated.append(leg)
    return validated
