"""Tests for India symbol registry used in market routing."""

from __future__ import annotations

from trade_integrations.autonomous_agents.market import symbol_execution_market
from trade_integrations.dataflows.company_research.india_symbols import (
    clear_india_symbol_cache,
    is_india_listed_symbol,
    load_india_symbols,
)
from trade_integrations.dataflows.symbol_registry.openalgo_registry import resolve_openalgo_db_path


def test_niftybees_is_india() -> None:
    clear_india_symbol_cache()
    assert is_india_listed_symbol("NIFTYBEES")
    assert symbol_execution_market("NIFTYBEES") == "IN"


def test_india_registry_includes_niftybees() -> None:
    clear_india_symbol_cache()
    symbols = load_india_symbols()
    assert "NIFTYBEES" in symbols
    assert "NIFTY" in symbols


def test_openalgo_symtoken_has_reliance() -> None:
    if resolve_openalgo_db_path() is None:
        return
    clear_india_symbol_cache()
    symbols = load_india_symbols()
    assert "RELIANCE" in symbols
