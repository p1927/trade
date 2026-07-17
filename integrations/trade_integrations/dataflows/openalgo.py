"""OpenAlgo live market-data adapter for Indian brokers.

TradingAgents talks to a locally running OpenAlgo instance
(http://127.0.0.1:5001 by default). OpenAlgo holds the broker session;
this module reads market data via the unified REST API and delegates
vendor fetch logic to ``trade_integrations.openalgo.market_data``.

Requires OPENALGO_API_KEY (generated inside OpenAlgo after login) and
OPENALGO_HOST. When OpenAlgo cannot serve a symbol (e.g. US tickers),
NoMarketDataError is raised so the vendor router can fall back to yfinance.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Annotated

import pandas as pd
from dateutil.relativedelta import relativedelta

from trade_integrations.openalgo.market_data import (
    fetch_history_raw,
    fetch_option_chain_raw as _fetch_option_chain_raw,
    fetch_option_expiry_dates,
    fetch_quote_raw as _fetch_live_quote_raw,
    openalgo_post as _openalgo_post,
)
from trade_integrations.openalgo.rest_client import openalgo_settings as _openalgo_settings
from trade_integrations.openalgo.symbols import normalize_openalgo_expiry, resolve_openalgo_symbol
from tradingagents.dataflows.errors import NoMarketDataError, VendorNotConfiguredError, VendorRateLimitError
from tradingagents.dataflows.stockstats_utils import _assert_ohlcv_not_stale
from tradingagents.dataflows.y_finance import get_stock_stats_indicators_window

logger = logging.getLogger(__name__)


def _fetch_history(symbol: str, start_date: str, end_date: str) -> pd.DataFrame:
    return fetch_history_raw(symbol, start_date, end_date, interval="D")


def _fetch_live_quote(oa_symbol: str, exchange: str) -> dict | None:
    from trade_integrations.openalgo.market_data import _quote_data

    return _quote_data(oa_symbol, exchange)


def fetch_openalgo_quote(symbol: str) -> dict | None:
    """Fetch a single live quote for an equity or index symbol (hub channel when registered)."""
    from trade_integrations.hub_capture.channel import get_quote

    return get_quote(symbol, _fetch_live_quote_raw)


def fetch_option_chain(
    underlying: str,
    exchange: str,
    *,
    expiry_date: str | None = None,
    strike_count: int | None = None,
) -> dict:
    """Fetch normalized option chain payload (hub channel when registered)."""
    from trade_integrations.hub_capture.channel import get_chain

    return get_chain(
        underlying,
        exchange,
        _fetch_option_chain_raw,
        expiry_date=expiry_date,
        strike_count=strike_count,
    )


def get_openalgo_stock_data(
    symbol: Annotated[str, "ticker symbol of the company"],
    start_date: Annotated[str, "Start date in yyyy-mm-dd format"],
    end_date: Annotated[str, "End date in yyyy-mm-dd format"],
) -> str:
    datetime.strptime(start_date, "%Y-%m-%d")
    datetime.strptime(end_date, "%Y-%m-%d")

    oa_symbol, exchange = resolve_openalgo_symbol(symbol)
    data = _fetch_history(symbol, start_date, end_date)
    _assert_ohlcv_not_stale(data, end_date, symbol, f"{oa_symbol}@{exchange}")

    for col in ("Open", "High", "Low", "Close"):
        if col in data.columns:
            data[col] = data[col].round(2)

    csv_string = data.to_csv(index=False)
    label = f"{oa_symbol}@{exchange}" if f"{oa_symbol}@{exchange}" != symbol.upper() else oa_symbol
    header = (
        f"# Stock data for {label} (live via OpenAlgo) from {start_date} to {end_date}\n"
        f"# Total records: {len(data)}\n"
        f"# Data retrieved on: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
    )

    from trade_integrations.hub_capture.channel import get_quote, resolve_registered_entity

    quote = get_quote(symbol, _fetch_live_quote_raw) if resolve_registered_entity(symbol) else None
    if quote is None:
        quote = _fetch_live_quote(oa_symbol, exchange)
    if quote and quote.get("ltp") is not None:
        header += (
            f"# Live quote: LTP={quote.get('ltp')} bid={quote.get('bid')} "
            f"ask={quote.get('ask')} volume={quote.get('volume')}\n"
        )
    header += "\n"

    return header + csv_string


def _load_openalgo_ohlcv(symbol: str, curr_date: str) -> pd.DataFrame:
    curr_dt = pd.to_datetime(curr_date)
    start = (curr_dt - relativedelta(years=5)).strftime("%Y-%m-%d")
    end = curr_date
    oa_symbol, exchange = resolve_openalgo_symbol(symbol)
    data = _fetch_history(symbol, start, end)
    data = data[data["Date"] <= curr_dt]
    _assert_ohlcv_not_stale(data, curr_date, symbol, f"{oa_symbol}@{exchange}")
    return data


def get_openalgo_indicators(
    symbol: Annotated[str, "ticker symbol of the company"],
    indicator: Annotated[str, "technical indicator to get the analysis and report of"],
    curr_date: Annotated[str, "The current trading date you are trading on, YYYY-mm-dd"],
    look_back_days: Annotated[int, "how many days to look back"],
) -> str:
    """Technical indicators computed from OpenAlgo daily history + stockstats."""
    from . import stockstats_utils

    original_loader = stockstats_utils.load_ohlcv
    try:
        stockstats_utils.load_ohlcv = _load_openalgo_ohlcv
        return get_stock_stats_indicators_window(symbol, indicator, curr_date, look_back_days)
    finally:
        stockstats_utils.load_ohlcv = original_loader
