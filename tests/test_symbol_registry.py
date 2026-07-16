"""Tests for broker-backed symbol registries."""

from __future__ import annotations

from trade_integrations.dataflows.symbol_registry.openalgo_registry import (
    clear_india_registry_cache,
    load_india_registry,
    resolve_openalgo_db_path,
)
from trade_integrations.dataflows.symbol_registry import (
    is_india_fno_underlying,
    is_india_listed_symbol,
    is_us_listed_symbol,
)


def test_openalgo_db_path_exists() -> None:
    path = resolve_openalgo_db_path()
    assert path is not None
    assert path.is_file()


def test_india_registry_loads_from_symtoken() -> None:
    clear_india_registry_cache()
    registry = load_india_registry(force_refresh=True)
    assert registry is not None
    assert registry.source == "openalgo_symtoken"
    assert "NIFTYBEES" in registry.cash_symbols
    assert "NIFTY" in registry.index_symbols
    assert "RELIANCE" in registry.cash_symbols


def test_niftybees_listed_via_registry() -> None:
    clear_india_registry_cache()
    assert is_india_listed_symbol("NIFTYBEES")


def test_nifty_fno_underlying() -> None:
    clear_india_registry_cache()
    assert is_india_fno_underlying("NIFTY")
    assert is_india_fno_underlying("BANKNIFTY")


def test_reliance_fno_underlying_when_db_present() -> None:
    clear_india_registry_cache()
    if resolve_openalgo_db_path() is None:
        return
    assert is_india_fno_underlying("RELIANCE")


def test_spy_us_registry() -> None:
    assert is_us_listed_symbol("SPY")


def test_unknown_us_not_india() -> None:
    clear_india_registry_cache()
    assert is_india_listed_symbol("SPY") is False
