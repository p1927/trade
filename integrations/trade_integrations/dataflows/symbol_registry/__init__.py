"""Unified symbol registry facade (OpenAlgo India + Alpaca US)."""

from __future__ import annotations

from trade_integrations.dataflows.symbol_registry.openalgo_indices import ALL_INDEX_SYMBOLS
from trade_integrations.dataflows.symbol_registry.openalgo_registry import (
    clear_india_registry_cache,
    is_india_fno_underlying,
    is_india_listed_symbol,
    load_india_registry,
    probe_india_symbol_live,
)


def is_us_listed_symbol(symbol: str) -> bool:
    from trade_integrations.dataflows.symbol_registry.alpaca_registry import (
        is_us_listed_symbol as _is_us,
    )

    return _is_us(symbol)


def load_us_registry(*, force_refresh: bool = False):
    from trade_integrations.dataflows.symbol_registry.alpaca_registry import load_us_registry as _load

    return _load(force_refresh=force_refresh)


def clear_us_registry_cache() -> None:
    from trade_integrations.dataflows.symbol_registry.alpaca_registry import clear_us_registry_cache as _clear

    _clear()


__all__ = [
    "ALL_INDEX_SYMBOLS",
    "clear_india_registry_cache",
    "clear_us_registry_cache",
    "is_india_fno_underlying",
    "is_india_listed_symbol",
    "is_us_listed_symbol",
    "load_india_registry",
    "load_us_registry",
    "probe_india_symbol_live",
]
