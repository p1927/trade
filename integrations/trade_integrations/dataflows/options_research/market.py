"""Instrument routing for India index and stock options."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from trade_integrations.dataflows.company_research.market import (
    Market,
    detect_market,
    normalize_ticker,
)
from trade_integrations.dataflows.symbol_registry import is_india_fno_underlying
from trade_integrations.dataflows.symbol_registry.openalgo_indices import ALL_INDEX_SYMBOLS

_INDEX_SYMBOLS = ALL_INDEX_SYMBOLS | frozenset({"NIFTY50", "BANKEX", "^NSEI", "^BSESN"})


class InstrumentType(str, Enum):
    INDEX = "index"
    STOCK = "stock"


@dataclass(frozen=True)
class OptionsInstrument:
    """Resolved symbols for options chain and execution."""

    input_ticker: str
    display_symbol: str
    instrument_type: InstrumentType
    market: Market
    underlying_symbol: str
    underlying_exchange: str
    options_exchange: str


def is_index_symbol(ticker: str) -> bool:
    raw = ticker.strip().upper()
    if raw.startswith("^"):
        raw = raw[1:]
    return raw in _INDEX_SYMBOLS


def is_options_research_eligible(ticker: str) -> bool:
    """Return True when OpenAlgo SymToken has F&O contracts for this underlying."""
    raw = ticker.strip().upper()
    if not raw:
        return False
    if raw.startswith("^"):
        raw = raw[1:]
    if is_index_symbol(raw):
        return True
    if is_india_fno_underlying(raw):
        return True
    if raw.endswith(".NS") or raw.endswith(".BO"):
        base = raw.rsplit(".", 1)[0]
        return is_india_fno_underlying(base)
    market = detect_market(ticker)
    return market == Market.IN and is_india_fno_underlying(raw)


def resolve_options_instrument(ticker: str) -> OptionsInstrument:
    """Map a user ticker to OpenAlgo chain/execution exchanges."""
    normalized = normalize_ticker(ticker)
    base = normalized.base_symbol
    if is_index_symbol(ticker):
        inst_type = InstrumentType.INDEX
        if base in ("SENSEX", "BANKEX"):
            underlying_exchange = "BSE_INDEX"
            options_exchange = "BFO"
        else:
            underlying_exchange = "NSE_INDEX"
            options_exchange = "NFO"
        underlying_symbol = base
    else:
        inst_type = InstrumentType.STOCK
        underlying_exchange = normalized.openalgo_exchange or "NSE"
        options_exchange = "BFO" if underlying_exchange == "BSE" else "NFO"
        underlying_symbol = base

    return OptionsInstrument(
        input_ticker=normalized.input_ticker,
        display_symbol=base,
        instrument_type=inst_type,
        market=normalized.market,
        underlying_symbol=underlying_symbol,
        underlying_exchange=underlying_exchange,
        options_exchange=options_exchange,
    )
