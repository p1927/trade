"""Cached NSE/BSE symbol universe for India vs US market routing."""

from __future__ import annotations

import logging

from .sources.resilience import load_bse_code_map

logger = logging.getLogger(__name__)

_IN_INDEX_TICKERS = frozenset(
    {
        "^NSEI",
        "^BSESN",
        "NIFTY",
        "NIFTY50",
        "BANKNIFTY",
        "FINNIFTY",
        "MIDCPNIFTY",
        "SENSEX",
    }
)

_SYMBOL_CACHE: frozenset[str] | None = None


def india_index_tickers() -> frozenset[str]:
    return _IN_INDEX_TICKERS


def load_india_symbols(*, force_refresh: bool = False) -> frozenset[str]:
    """Return upper-case symbols treated as India-listed (NSE equity list + BSE map)."""
    global _SYMBOL_CACHE
    if _SYMBOL_CACHE is not None and not force_refresh:
        return _SYMBOL_CACHE

    symbols: set[str] = set(_IN_INDEX_TICKERS)
    symbols.update(load_bse_code_map().keys())

    try:
        from nselib import capital_market

        frame = capital_market.equity_list()
        column = "SYMBOL" if "SYMBOL" in frame.columns else None
        if column:
            symbols.update(
                frame[column].astype(str).str.strip().str.upper().tolist()
            )
    except Exception as exc:
        logger.info("nselib equity_list unavailable for market routing: %s", exc)

    _SYMBOL_CACHE = frozenset(symbols)
    return _SYMBOL_CACHE


def is_india_listed_symbol(symbol: str) -> bool:
    raw = symbol.strip().upper()
    if not raw:
        return False
    if raw.endswith(".NS") or raw.endswith(".BO"):
        return True
    if raw in _IN_INDEX_TICKERS:
        return True
    base = raw.rsplit(".", 1)[0] if raw.endswith((".NS", ".BO")) else raw
    return base in load_india_symbols()


def clear_india_symbol_cache() -> None:
    """Test helper to reset the in-process cache."""
    global _SYMBOL_CACHE
    _SYMBOL_CACHE = None
