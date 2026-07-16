"""Market detection and ticker normalization (India-first, dual US+IN)."""

from __future__ import annotations

import os
from dataclasses import dataclass
from enum import Enum

from trade_integrations.dataflows.openalgo import resolve_openalgo_symbol

from .india_symbols import is_india_listed_symbol
from .us_symbols import is_us_known_symbol


class Market(str, Enum):
    IN = "IN"
    US = "US"


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


@dataclass(frozen=True)
class NormalizedTicker:
    """Ticker forms used by different backends for the same instrument."""

    input_ticker: str
    market: Market
    base_symbol: str
    openalgo_symbol: str
    openalgo_exchange: str
    yfinance_symbol: str
    display_symbol: str


def india_index_tickers() -> frozenset[str]:
    return _IN_INDEX_TICKERS


def detect_market(
    ticker: str,
    *,
    market_default: str | None = None,
    market_hint: Market | None = None,
) -> Market:
    """Classify a ticker as Indian (NSE/BSE) or US."""
    if market_hint is not None:
        return market_hint

    raw = ticker.strip().upper()
    if not raw:
        raise ValueError("ticker must be non-empty")

    if raw.endswith(".NS") or raw.endswith(".BO"):
        return Market.IN
    if raw in _IN_INDEX_TICKERS:
        return Market.IN
    if "." in raw:
        # e.g. BRK.B — treat dotted US-style tickers as US.
        return Market.US

    if is_india_listed_symbol(raw):
        return Market.IN

    if is_us_known_symbol(raw):
        return Market.US

    default = (market_default or os.getenv("TRADINGAGENTS_RESEARCH_MARKET_DEFAULT", "IN")).upper()
    if default == "US":
        return Market.US
    return Market.IN


def _base_symbol(ticker: str, market: Market) -> str:
    raw = ticker.strip().upper()
    if raw.endswith(".NS") or raw.endswith(".BO"):
        return raw.rsplit(".", 1)[0]
    if raw in _IN_INDEX_TICKERS and raw.startswith("^"):
        return raw[1:]
    return raw


def normalize_ticker(
    ticker: str,
    *,
    market: Market | None = None,
    market_hint: Market | None = None,
    market_default: str | None = None,
) -> NormalizedTicker:
    """Return backend-specific symbol forms for one instrument."""
    raw = ticker.strip().upper()
    resolved_market = market or market_hint or detect_market(
        raw, market_default=market_default
    )
    base = _base_symbol(raw, resolved_market)

    if resolved_market is Market.IN:
        try:
            openalgo_symbol, openalgo_exchange = resolve_openalgo_symbol(raw)
        except Exception:
            openalgo_symbol, openalgo_exchange = base, "NSE"
        if raw.endswith(".BO"):
            yfinance_symbol = f"{base}.BO"
        elif openalgo_exchange == "BSE":
            yfinance_symbol = f"{base}.BO"
        else:
            yfinance_symbol = f"{base}.NS"
        display_symbol = base
    else:
        openalgo_symbol = base
        openalgo_exchange = ""
        yfinance_symbol = base
        display_symbol = base

    return NormalizedTicker(
        input_ticker=raw,
        market=resolved_market,
        base_symbol=base,
        openalgo_symbol=openalgo_symbol,
        openalgo_exchange=openalgo_exchange,
        yfinance_symbol=yfinance_symbol,
        display_symbol=display_symbol,
    )
