"""OpenAlgo live market-data adapter for Indian brokers.

TradingAgents talks to a locally running OpenAlgo instance
(http://127.0.0.1:5000 by default). OpenAlgo holds the broker session
(e.g. Groww); this module only reads market data via the unified REST API.

Requires OPENALGO_API_KEY (generated inside OpenAlgo after login) and
OPENALGO_HOST. When OpenAlgo cannot serve a symbol (e.g. US tickers),
NoMarketDataError is raised so the vendor router can fall back to yfinance.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime

import pandas as pd
import requests
from dateutil.relativedelta import relativedelta
from typing import Annotated

from tradingagents.dataflows.config import get_config
from tradingagents.dataflows.errors import NoMarketDataError, VendorNotConfiguredError, VendorRateLimitError
from tradingagents.dataflows.stockstats_utils import _assert_ohlcv_not_stale, _clean_dataframe
from tradingagents.dataflows.y_finance import get_stock_stats_indicators_window

logger = logging.getLogger(__name__)

# Yahoo / TradingAgents aliases -> OpenAlgo (symbol, exchange)
# Index symbols use NSE_INDEX per OpenAlgo docs (docs.openalgo.in/symbol-format)
_OPENALGO_ALIASES: dict[str, tuple[str, str]] = {
    "^NSEI": ("NIFTY", "NSE_INDEX"),
    "NIFTY50": ("NIFTY", "NSE_INDEX"),
    "^BSESN": ("SENSEX", "BSE_INDEX"),
    "NIFTY": ("NIFTY", "NSE_INDEX"),
    "BANKNIFTY": ("BANKNIFTY", "NSE_INDEX"),
    "FINNIFTY": ("FINNIFTY", "NSE_INDEX"),
    "MIDCPNIFTY": ("MIDCPNIFTY", "NSE_INDEX"),
    "SENSEX": ("SENSEX", "BSE_INDEX"),
}


def resolve_openalgo_symbol(symbol: str) -> tuple[str, str]:
    """Map a TradingAgents ticker to OpenAlgo symbol + exchange."""
    raw = symbol.strip().upper()
    if raw in _OPENALGO_ALIASES:
        return _OPENALGO_ALIASES[raw]
    if raw.endswith(".NS"):
        return raw[:-3], "NSE"
    if raw.endswith(".BO"):
        return raw[:-3], "BSE"
    if raw.startswith("^"):
        raise NoMarketDataError(
            symbol,
            raw,
            f"index {raw!r} is not mapped for OpenAlgo (use NIFTY / BANKNIFTY or *.NS)",
        )
    # Plain equity symbols default to NSE (RELIANCE, SBIN, …).
    return raw, "NSE"


def _openalgo_settings() -> tuple[str, str]:
    from trade_integrations.env import load_trade_env

    load_trade_env()
    config = get_config()
    host = (config.get("openalgo_host") or os.getenv("OPENALGO_HOST") or "http://127.0.0.1:5001").rstrip("/")
    api_key = config.get("openalgo_api_key") or os.getenv("OPENALGO_API_KEY") or ""
    if not api_key:
        raise VendorNotConfiguredError(
            "OPENALGO_API_KEY is not set. Start OpenAlgo, log in, generate an API "
            "key in the dashboard, and add it to your .env file."
        )
    return host, api_key


def _openalgo_post(endpoint: str, payload: dict) -> dict:
    host, api_key = _openalgo_settings()
    body = {**payload, "apikey": api_key}
    url = f"{host}/api/v1/{endpoint}"
    try:
        response = requests.post(url, json=body, timeout=30)
    except requests.RequestException as exc:
        raise NoMarketDataError(
            payload.get("symbol", "?"),
            payload.get("symbol"),
            f"OpenAlgo request failed ({url}): {exc}",
        ) from exc

    if response.status_code == 429:
        raise VendorRateLimitError(f"OpenAlgo rate limited: {endpoint}")

    try:
        parsed = response.json()
    except ValueError as exc:
        raise NoMarketDataError(
            payload.get("symbol", "?"),
            payload.get("symbol"),
            f"OpenAlgo returned non-JSON from {endpoint}",
        ) from exc

    if response.status_code >= 400 or parsed.get("status") != "success":
        message = parsed.get("message") or parsed.get("error") or response.text[:200]
        raise NoMarketDataError(
            payload.get("symbol", "?"),
            payload.get("symbol"),
            f"OpenAlgo {endpoint} error: {message}",
        )
    return parsed


def _fetch_history(symbol: str, start_date: str, end_date: str) -> pd.DataFrame:
    oa_symbol, oa_exchange = resolve_openalgo_symbol(symbol)
    parsed = _openalgo_post(
        "history",
        {
            "symbol": oa_symbol,
            "exchange": oa_exchange,
            "interval": "D",
            "start_date": start_date,
            "end_date": end_date,
        },
    )
    rows = parsed.get("data") or []
    if not rows:
        raise NoMarketDataError(symbol, f"{oa_symbol}@{oa_exchange}", "OpenAlgo history returned no rows")

    frame = pd.DataFrame(rows)
    frame = frame.rename(
        columns={
            "timestamp": "Date",
            "open": "Open",
            "high": "High",
            "low": "Low",
            "close": "Close",
            "volume": "Volume",
        }
    )
    frame["Date"] = pd.to_datetime(frame["Date"], errors="coerce")
    if frame["Date"].dt.tz is not None:
        frame["Date"] = frame["Date"].dt.tz_localize(None)
    frame = frame.dropna(subset=["Date"])
    return _clean_dataframe(frame)


def _fetch_live_quote(oa_symbol: str, exchange: str) -> dict | None:
    try:
        parsed = _openalgo_post("quotes", {"symbol": oa_symbol, "exchange": exchange})
        return parsed.get("data")
    except (NoMarketDataError, VendorNotConfiguredError, VendorRateLimitError):
        return None


def _fetch_live_quote_raw(symbol: str) -> dict | None:
    """Direct OpenAlgo quote fetch (no hub channel)."""
    oa_symbol, exchange = resolve_openalgo_symbol(symbol)
    data = _fetch_live_quote(oa_symbol, exchange)
    if not data:
        return None
    return {
        "ltp": data.get("ltp") or data.get("last_price"),
        "volume": data.get("volume"),
        "change_pct": data.get("change_percent") or data.get("change_pct"),
        "high_52w": data.get("high_52w"),
        "low_52w": data.get("low_52w"),
        "source": "openalgo",
    }


def fetch_openalgo_quote(symbol: str) -> dict | None:
    """Fetch a single live quote for an equity or index symbol (hub channel when registered)."""
    from trade_integrations.hub_capture.channel import get_quote

    return get_quote(symbol, _fetch_live_quote_raw)


def normalize_openalgo_expiry(expiry: str) -> str:
    """Convert OpenAlgo expiry (DD-MMM-YY or DDMMMYY) to DDMMMYY for optionchain."""
    raw = expiry.strip().upper().replace("-", "")
    return raw


def _unwrap_openalgo_market_payload(parsed: dict) -> dict:
    """OpenAlgo returns option chain fields at top level; expiry dates under data."""
    data = parsed.get("data")
    if isinstance(data, dict) and data.get("chain"):
        return data
    if parsed.get("chain"):
        return parsed
    if isinstance(data, list):
        return {"chain": data}
    return data if isinstance(data, dict) else {}


def fetch_option_expiry_dates(
    symbol: str,
    exchange: str = "NFO",
    *,
    instrument_type: str = "options",
) -> list[str]:
    """Return available option expiry dates (DD-MMM-YY or DDMMMYY) from OpenAlgo."""
    parsed = _openalgo_post(
        "expiry",
        {
            "symbol": symbol.upper(),
            "exchange": exchange.upper(),
            "instrumenttype": instrument_type,
        },
    )
    data = parsed.get("data") or {}
    if isinstance(data, list):
        return [str(x) for x in data]
    return list(data.get("expiry_dates") or data.get("expiries") or [])


def _fetch_option_chain_raw(
    underlying: str,
    exchange: str,
    *,
    expiry_date: str | None = None,
    strike_count: int | None = None,
) -> dict:
    """Direct OpenAlgo option chain fetch (no hub channel)."""
    body: dict = {
        "underlying": underlying.upper(),
        "exchange": exchange.upper(),
    }
    if expiry_date:
        body["expiry_date"] = normalize_openalgo_expiry(expiry_date)
    if strike_count is not None:
        body["strike_count"] = strike_count
    parsed = _openalgo_post("optionchain", body)
    meta = _unwrap_openalgo_market_payload(parsed)
    chain = meta.get("chain") or []
    ce_oi = sum(int(row.get("ce", {}).get("oi") or 0) for row in chain if isinstance(row, dict))
    pe_oi = sum(int(row.get("pe", {}).get("oi") or 0) for row in chain if isinstance(row, dict))
    pcr = round(pe_oi / ce_oi, 4) if ce_oi else None
    return {
        "underlying": meta.get("underlying") or underlying.upper(),
        "underlying_ltp": meta.get("underlying_ltp"),
        "expiry_date": meta.get("expiry_date") or body.get("expiry_date") or expiry_date,
        "atm_strike": meta.get("atm_strike"),
        "chain": chain,
        "pcr": pcr,
        "total_call_oi": ce_oi,
        "total_put_oi": pe_oi,
        "source": "openalgo",
    }


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
    # Reuse indicator descriptions / formatting from the yfinance path, but
    # override OHLCV loading by temporarily patching load_ohlcv.
    from . import stockstats_utils

    original_loader = stockstats_utils.load_ohlcv
    try:
        stockstats_utils.load_ohlcv = _load_openalgo_ohlcv
        return get_stock_stats_indicators_window(symbol, indicator, curr_date, look_back_days)
    finally:
        stockstats_utils.load_ohlcv = original_loader
