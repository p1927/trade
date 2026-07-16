"""Cached NSE/BSE symbol universe for India vs US market routing."""

from __future__ import annotations

import logging

from trade_integrations.dataflows.symbol_registry.openalgo_indices import ALL_INDEX_SYMBOLS
from trade_integrations.dataflows.symbol_registry.openalgo_registry import (
    clear_india_registry_cache,
    is_india_listed_symbol as _registry_is_india_listed,
    load_india_registry,
)

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
) | ALL_INDEX_SYMBOLS

_NSELIB_CACHE: frozenset[str] | None = None


def india_index_tickers() -> frozenset[str]:
    return _IN_INDEX_TICKERS


def _load_nselib_fallback() -> frozenset[str]:
    global _NSELIB_CACHE
    if _NSELIB_CACHE is not None:
        return _NSELIB_CACHE
    symbols: set[str] = set(_IN_INDEX_TICKERS)
    try:
        from nselib import capital_market

        frame = capital_market.equity_list()
        column = "SYMBOL" if "SYMBOL" in frame.columns else None
        if column:
            symbols.update(frame[column].astype(str).str.strip().str.upper().tolist())
    except Exception as exc:
        logger.info("nselib equity_list unavailable for market routing fallback: %s", exc)
    _NSELIB_CACHE = frozenset(symbols)
    return _NSELIB_CACHE


def load_india_symbols(*, force_refresh: bool = False) -> frozenset[str]:
    """Return upper-case India-listed symbols (OpenAlgo SymToken primary)."""
    if force_refresh:
        clear_india_registry_cache()
    registry = load_india_registry(force_refresh=force_refresh)
    if registry is not None:
        return registry.cash_symbols | registry.index_symbols
    return _load_nselib_fallback()


def is_india_listed_symbol(symbol: str) -> bool:
    if _registry_is_india_listed(symbol):
        return True
    raw = symbol.strip().upper()
    if not raw:
        return False
    if raw.endswith(".NS") or raw.endswith(".BO"):
        return True
    if raw in _IN_INDEX_TICKERS:
        return True
    base = raw.rsplit(".", 1)[0] if raw.endswith((".NS", ".BO")) else raw
    return base in _load_nselib_fallback()


def clear_india_symbol_cache() -> None:
    """Test helper to reset in-process caches."""
    global _NSELIB_CACHE
    clear_india_registry_cache()
    _NSELIB_CACHE = None
