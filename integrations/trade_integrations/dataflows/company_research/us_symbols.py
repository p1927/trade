"""US-listed symbol detection via Alpaca assets registry."""

from __future__ import annotations

from trade_integrations.dataflows.symbol_registry.alpaca_registry import (
    is_us_listed_symbol as _registry_is_us_listed,
    load_us_registry,
)


def is_us_known_symbol(symbol: str) -> bool:
    return _registry_is_us_listed(symbol)


def known_us_symbols() -> frozenset[str]:
    return load_us_registry().symbols
