"""OpenAlgo live market-data adapter for Indian brokers.

TradingAgents and the index prediction pipeline talk to a locally running
OpenAlgo instance (http://127.0.0.1:5001 by default). OpenAlgo holds the
broker session (INDmoney / INDstocks when configured); this module reads
market data via the unified REST API backed by INDstocks endpoints for
quotes, historical OHLCV, and option chains.

Requires OPENALGO_API_KEY (generated inside OpenAlgo after login) and
OPENALGO_HOST. India symbols use OpenAlgo/INDstocks as primary; yfinance
is enrichment/fallback when OpenAlgo is down or a field is unavailable
(e.g. trailing P/E). US tickers still raise NoMarketDataError for vendor
routing to Alpaca/yfinance.
"""

from __future__ import annotations

import logging
import time
from datetime import date, datetime, timedelta
from typing import Annotated, Any

import pandas as pd
from dateutil.relativedelta import relativedelta

from trade_integrations.openalgo.market_data import (
    fetch_history_raw,
    fetch_option_chain_raw as _fetch_option_chain_raw,
    fetch_option_expiry_dates,
    fetch_quote_raw as _fetch_live_quote_raw,
    openalgo_configured as _openalgo_configured,
    openalgo_post as _openalgo_post,
)
from trade_integrations.openalgo.rest_client import openalgo_settings as _openalgo_settings
from trade_integrations.openalgo.symbols import normalize_openalgo_expiry, resolve_openalgo_symbol
from tradingagents.dataflows.errors import NoMarketDataError, VendorNotConfiguredError, VendorRateLimitError
from tradingagents.dataflows.stockstats_utils import _assert_ohlcv_not_stale, _clean_dataframe
from tradingagents.dataflows.y_finance import get_stock_stats_indicators_window

logger = logging.getLogger(__name__)

# Gentle spacing when batch-fetching many INDstocks history calls (~3 req/s server-side).
_BATCH_HISTORY_SLEEP_S = 0.12


def fetch_openalgo_history(
    symbol: str,
    start_date: str,
    end_date: str,
    *,
    interval: str = "D",
) -> pd.DataFrame:
    """Fetch OHLCV from OpenAlgo → INDmoney/INDstocks historical API."""
    return fetch_history_raw(symbol, start_date, end_date, interval=interval)


def _yfinance_ticker(symbol: str) -> str:
    """Map an India symbol to the yfinance enrichment ticker."""
    raw = symbol.strip().upper()
    if raw in ("NIFTY", "NIFTY50", "^NSEI"):
        return "^NSEI"
    if raw in ("INDIAVIX", "^INDIAVIX"):
        return "^INDIAVIX"
    if raw.endswith(".NS") or raw.endswith(".BO"):
        return raw
    return f"{raw}.NS"


def _fetch_yfinance_history_india(symbol: str, start_date: str, end_date: str) -> pd.DataFrame:
    """yfinance enrichment fallback for India equities and indices."""
    import yfinance as yf

    yf_sym = _yfinance_ticker(symbol)
    hist = yf.Ticker(yf_sym).history(start=start_date, end=end_date, auto_adjust=True)
    if hist is None or hist.empty:
        raise NoMarketDataError(symbol, yf_sym, "yfinance history returned no rows")

    frame = hist.reset_index()
    date_col = next(
        (col for col in ("Date", "Datetime", "index") if col in frame.columns),
        frame.columns[0],
    )
    rename = {date_col: "Date"}
    for src, dst in (("Open", "Open"), ("High", "High"), ("Low", "Low"), ("Close", "Close"), ("Volume", "Volume")):
        if src in frame.columns:
            rename[src] = dst
    frame = frame.rename(columns=rename)
    frame["Date"] = pd.to_datetime(frame["Date"], errors="coerce")
    if frame["Date"].dt.tz is not None:
        frame["Date"] = frame["Date"].dt.tz_localize(None)
    frame = frame.dropna(subset=["Date"])
    return _clean_dataframe(frame)


def to_index_research_frame(frame: pd.DataFrame) -> pd.DataFrame:
    """Normalize OpenAlgo or yfinance OHLCV to index_research columns (date, close, …)."""
    if frame is None or frame.empty:
        return pd.DataFrame(columns=["date", "close"])

    working = frame.copy()
    if "Date" in working.columns:
        date_series = working["Date"]
    elif "date" in working.columns:
        date_series = working["date"]
    elif "Datetime" in working.columns:
        date_series = working["Datetime"]
    else:
        date_series = working.iloc[:, 0]

    out = pd.DataFrame()
    out["date"] = pd.to_datetime(date_series, errors="coerce").dt.strftime("%Y-%m-%d")
    for src_upper, src_lower, dst in (
        ("Close", "close", "close"),
        ("Open", "open", "open"),
        ("High", "high", "high"),
        ("Low", "low", "low"),
        ("Volume", "volume", "volume"),
    ):
        if src_upper in working.columns:
            out[dst] = working[src_upper].astype(float)
        elif src_lower in working.columns:
            out[dst] = working[src_lower].astype(float)

    out = out.dropna(subset=["date"])
    if "close" not in out.columns:
        return pd.DataFrame(columns=["date", "close"])

    cols = ["date", "close"]
    for optional in ("high", "low", "open", "volume"):
        if optional in out.columns:
            cols.append(optional)
    return out[cols].sort_values("date").reset_index(drop=True)


def load_india_ohlcv(
    symbol: str,
    days: int = 365,
    *,
    interval: str = "D",
    end_date: str | None = None,
    start_date: str | None = None,
    force_refresh: bool = False,
    return_provenance: bool = False,
) -> pd.DataFrame | tuple[pd.DataFrame, dict[str, Any]]:
    """Load India OHLCV: hub cache → OpenAlgo/INDstocks → yfinance enrichment."""
    from trade_integrations.hub_capture.ohlcv_cache import (
        merge_with_cache,
        read_cached_bars,
    )

    end = end_date or date.today().isoformat()
    start = start_date or (date.fromisoformat(end) - timedelta(days=max(int(days), 1))).isoformat()
    provenance: dict[str, Any] = {
        "symbol": symbol,
        "start_date": start,
        "end_date": end,
        "used_cache": False,
        "vendor_fetch": False,
        "source": None,
        "vendor": None,
        "final_rows": 0,
    }

    if interval.upper() in ("D", "1D", "DAY") and not force_refresh:
        cached, cache_meta = read_cached_bars(symbol, start, end)
        provenance.update(cache_meta)
        if not cached.empty:
            provenance["used_cache"] = True
            provenance["source"] = "hub_cache"
            provenance["final_rows"] = int(len(cached))
            out = _strip_cache_columns(cached)
            if return_provenance:
                return out, provenance
            return out

    fetched: pd.DataFrame | None = None
    vendor = None
    source = None

    if _openalgo_configured():
        try:
            raw = fetch_openalgo_history(symbol, start, end, interval=interval)
            fetched = to_index_research_frame(raw)
            if not fetched.empty:
                vendor = "openalgo"
                source = "openalgo"
        except (NoMarketDataError, VendorNotConfiguredError, VendorRateLimitError) as exc:
            logger.debug("OpenAlgo history failed for %s: %s", symbol, exc)

    if fetched is None or fetched.empty:
        try:
            raw = _fetch_yfinance_history_india(symbol, start, end)
            fetched = to_index_research_frame(raw)
            vendor = "yfinance"
            source = "yfinance_enrichment"
        except Exception as exc:
            logger.debug("yfinance history fallback failed for %s: %s", symbol, exc)
            fetched = pd.DataFrame(columns=["date", "close"])

    if interval.upper() in ("D", "1D", "DAY") and not fetched.empty and vendor:
        merged, provenance = merge_with_cache(
            symbol,
            start,
            end,
            fetched,
            source=str(source),
            vendor=str(vendor),
            cache_before=provenance,
        )
        out = _strip_cache_columns(merged)
    else:
        out = fetched
        provenance["vendor_fetch"] = bool(not fetched.empty)
        provenance["vendor"] = vendor
        provenance["source"] = source
        provenance["final_rows"] = int(len(out))

    if return_provenance:
        return out, provenance
    return out


def _strip_cache_columns(frame: pd.DataFrame) -> pd.DataFrame:
    cols = ["date", "close"]
    for optional in ("high", "low", "open", "volume"):
        if optional in frame.columns:
            cols.append(optional)
    return frame[cols].sort_values("date").reset_index(drop=True)


def compute_close_return_pct(frame: pd.DataFrame, *, lookback_sessions: int = 7) -> float | None:
    """Close-to-close return (%) over the last ``lookback_sessions`` trading rows."""
    if frame.empty or "close" not in frame.columns:
        return None
    closes = frame["close"].astype(float).dropna()
    if len(closes) < 2:
        return None
    offset = min(lookback_sessions, len(closes) - 1)
    first = float(closes.iloc[-offset - 1])
    last = float(closes.iloc[-1])
    if first <= 0:
        return None
    return (last - first) / first * 100.0


def batch_load_india_ohlcv(
    symbols: list[str],
    days: int = 14,
    *,
    interval: str = "D",
    force_refresh: bool = False,
) -> dict[str, pd.DataFrame]:
    """Fetch OHLCV for many symbols (hub cache → OpenAlgo → yfinance)."""
    out: dict[str, pd.DataFrame] = {}
    for idx, symbol in enumerate(symbols):
        if idx > 0:
            time.sleep(_BATCH_HISTORY_SLEEP_S)
        base = symbol.strip().upper().replace(".NS", "").replace(".BO", "")
        frame = load_india_ohlcv(
            symbol,
            days=days,
            interval=interval,
            force_refresh=force_refresh,
        )
        if not frame.empty:
            out[base] = frame
    return out


def fetch_openalgo_live_snapshot(symbol: str) -> dict[str, Any] | None:
    """Live quote snapshot from OpenAlgo/INDstocks (LTP, change %, volume)."""
    quote = _fetch_live_quote_raw(symbol)
    if not quote or quote.get("ltp") is None:
        return None
    return {
        "ltp": float(quote["ltp"]),
        "change_pct": quote.get("change_pct"),
        "volume": quote.get("volume"),
        "high_52w": quote.get("high_52w"),
        "low_52w": quote.get("low_52w"),
        "source": "openalgo",
    }


def _fetch_history(symbol: str, start_date: str, end_date: str) -> pd.DataFrame:
    return fetch_openalgo_history(symbol, start_date, end_date, interval="D")


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
