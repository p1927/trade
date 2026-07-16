"""Alpaca paper/live market-data adapter for US equities.

TradingAgents and OpenAlgo MCP use this module for US tickers when Alpaca keys
are configured. Indian symbols continue through OpenAlgo; Alpaca is tried only
for US markets (or when OpenAlgo raises NoMarketDataError in the vendor chain).

Paper vs live is structural: ``ALPACA_PROFILE=paper`` uses
``paper-api.alpaca.markets``; live profiles use ``api.alpaca.markets``.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime
from typing import Annotated, Any

import pandas as pd
import requests
from dateutil.relativedelta import relativedelta

from trade_integrations.dataflows.company_research.market import Market, detect_market
from tradingagents.dataflows.errors import NoMarketDataError, VendorNotConfiguredError, VendorRateLimitError
from tradingagents.dataflows.stockstats_utils import _assert_ohlcv_not_stale, _clean_dataframe
from tradingagents.dataflows.y_finance import get_stock_stats_indicators_window

logger = logging.getLogger(__name__)

PAPER_HOST = "https://paper-api.alpaca.markets"
LIVE_HOST = "https://api.alpaca.markets"
DATA_HOST = "https://data.alpaca.markets"


def _settings() -> dict[str, str]:
    profile = (os.getenv("ALPACA_PROFILE") or "paper").strip().lower()
    is_paper = profile == "paper"
    return {
        "api_key": (os.getenv("ALPACA_API_KEY") or "").strip(),
        "secret": (
            os.getenv("ALPACA_API_SECRET")
            or os.getenv("ALPACA_SECRET_KEY")
            or ""
        ).strip(),
        "trade_base": (
            os.getenv("ALPACA_API_BASE")
            or (PAPER_HOST if is_paper else LIVE_HOST)
        ).rstrip("/"),
        "data_base": (os.getenv("ALPACA_DATA_BASE") or DATA_HOST).rstrip("/"),
        "feed": (os.getenv("ALPACA_DATA_FEED") or "iex").strip().lower(),
        "profile": profile,
        "realtime": (os.getenv("ALPACA_REALTIME_ENABLED") or "true").strip().lower()
        in ("1", "true", "yes"),
    }


def alpaca_configured() -> bool:
    cfg = _settings()
    return bool(cfg["api_key"] and cfg["secret"])


def _headers() -> dict[str, str]:
    cfg = _settings()
    if not cfg["api_key"] or not cfg["secret"]:
        raise VendorNotConfiguredError(
            "ALPACA_API_KEY and ALPACA_API_SECRET are not set. "
            "Add paper keys from https://app.alpaca.markets/ to your .env file."
        )
    return {
        "APCA-API-KEY-ID": cfg["api_key"],
        "APCA-API-SECRET-KEY": cfg["secret"],
    }


def _request(
    method: str,
    url: str,
    *,
    params: dict[str, Any] | None = None,
    json_body: dict[str, Any] | None = None,
    symbol: str = "?",
) -> dict[str, Any]:
    try:
        response = requests.request(
            method,
            url,
            headers=_headers(),
            params=params,
            json=json_body,
            timeout=30,
        )
    except requests.RequestException as exc:
        raise NoMarketDataError(symbol, symbol, f"Alpaca request failed: {exc}") from exc

    if response.status_code == 429:
        raise VendorRateLimitError(f"Alpaca rate limited: {url}")

    try:
        payload = response.json() if response.content else {}
    except ValueError as exc:
        raise NoMarketDataError(symbol, symbol, "Alpaca returned non-JSON response") from exc

    if response.status_code >= 400:
        message = payload.get("message") or payload.get("error") or response.text[:200]
        raise NoMarketDataError(symbol, symbol, f"Alpaca error ({response.status_code}): {message}")

    return payload if isinstance(payload, dict) else {}


def _normalize_us_symbol(symbol: str) -> str:
    raw = symbol.strip().upper()
    if raw.endswith(".NS") or raw.endswith(".BO"):
        raise NoMarketDataError(
            raw,
            raw,
            f"{raw!r} is an Indian ticker; Alpaca serves US equities only.",
        )
    market = detect_market(raw)
    if market is Market.IN:
        raise NoMarketDataError(
            raw,
            raw,
            f"{raw!r} is classified as IN market; use OpenAlgo for Indian symbols.",
        )
    return raw.replace(".", "-") if "." in raw else raw


def fetch_alpaca_quote(symbol: str) -> dict[str, Any] | None:
    """Fetch latest bid/ask quote for a US symbol; None when realtime is disabled."""
    cfg = _settings()
    if not cfg["realtime"] or not alpaca_configured():
        return None

    clean = _normalize_us_symbol(symbol)
    url = f"{cfg['data_base']}/v2/stocks/{clean}/quotes/latest"
    try:
        payload = _request("GET", url, params={"feed": cfg["feed"]}, symbol=clean)
    except (NoMarketDataError, VendorNotConfiguredError, VendorRateLimitError):
        raise
    except Exception:
        logger.debug("alpaca quote failed for %s", clean, exc_info=True)
        return None

    quote = payload.get("quote") or {}
    bid = quote.get("bp")
    ask = quote.get("ap")
    ltp = None
    if bid is not None and ask is not None:
        ltp = (float(bid) + float(ask)) / 2.0
    elif bid is not None:
        ltp = float(bid)
    elif ask is not None:
        ltp = float(ask)

    return {
        "ltp": ltp,
        "bid": bid,
        "ask": ask,
        "bid_size": quote.get("bs"),
        "ask_size": quote.get("as"),
        "volume": None,
        "change_pct": None,
        "source": "alpaca",
        "feed": cfg["feed"],
        "profile": cfg["profile"],
    }


def fetch_alpaca_trade_snapshot(symbol: str) -> dict[str, Any] | None:
    """Fetch latest trade price (often fresher than quote mid for liquid names)."""
    cfg = _settings()
    if not cfg["realtime"] or not alpaca_configured():
        return None

    clean = _normalize_us_symbol(symbol)
    url = f"{cfg['data_base']}/v2/stocks/{clean}/trades/latest"
    try:
        payload = _request("GET", url, params={"feed": cfg["feed"]}, symbol=clean)
    except NoMarketDataError:
        return None

    trade = payload.get("trade") or {}
    price = trade.get("p")
    if price is None:
        return None
    return {
        "ltp": float(price),
        "volume": trade.get("s"),
        "source": "alpaca_trade",
        "feed": cfg["feed"],
        "profile": cfg["profile"],
    }


def fetch_alpaca_account() -> dict[str, Any]:
    """Return paper/live account summary from the trading API."""
    cfg = _settings()
    url = f"{cfg['trade_base']}/v2/account"
    payload = _request("GET", url, symbol="account")
    return {
        "profile": cfg["profile"],
        "account_number": payload.get("account_number"),
        "status": payload.get("status"),
        "currency": payload.get("currency"),
        "cash": payload.get("cash"),
        "equity": payload.get("equity"),
        "buying_power": payload.get("buying_power"),
        "portfolio_value": payload.get("portfolio_value"),
        "trading_blocked": payload.get("trading_blocked"),
        "pattern_day_trader": payload.get("pattern_day_trader"),
        "source": "alpaca",
    }


def list_alpaca_positions() -> list[dict[str, Any]]:
    """Open Alpaca equity positions (paper or live per ALPACA_PROFILE)."""
    cfg = _settings()
    url = f"{cfg['trade_base']}/v2/positions"
    try:
        response = requests.get(url, headers=_headers(), timeout=30)
    except requests.RequestException as exc:
        raise NoMarketDataError("positions", "positions", f"Alpaca request failed: {exc}") from exc
    if response.status_code >= 400:
        raise NoMarketDataError("positions", "positions", response.text[:200])
    payload = response.json()
    if isinstance(payload, list):
        return [r for r in payload if isinstance(r, dict)]
    return []


def submit_alpaca_market_order(
    symbol: str,
    *,
    side: str,
    qty: float,
) -> dict[str, Any]:
    """Submit a day market order via Alpaca trading API."""
    clean = _normalize_us_symbol(symbol)
    if side.lower() not in {"buy", "sell"}:
        raise ValueError(f"invalid side: {side}")
    if qty <= 0:
        raise ValueError("qty must be positive")
    cfg = _settings()
    url = f"{cfg['trade_base']}/v2/orders"
    body = {
        "symbol": clean,
        "qty": str(qty),
        "side": side.lower(),
        "type": "market",
        "time_in_force": "day",
    }
    return _request("POST", url, symbol=clean, json_body=body)


def close_alpaca_position(symbol: str) -> dict[str, Any]:
    """Close an entire Alpaca position for symbol."""
    clean = _normalize_us_symbol(symbol)
    cfg = _settings()
    url = f"{cfg['trade_base']}/v2/positions/{clean}"
    return _request("DELETE", url, symbol=clean)


def _fetch_bars(symbol: str, start_date: str, end_date: str) -> pd.DataFrame:
    clean = _normalize_us_symbol(symbol)
    cfg = _settings()
    url = f"{cfg['data_base']}/v2/stocks/{clean}/bars"
    params = {
        "timeframe": "1Day",
        "start": f"{start_date}T00:00:00Z",
        "end": f"{end_date}T23:59:59Z",
        "limit": 10000,
        "feed": cfg["feed"],
        "adjustment": "split",
    }
    payload = _request("GET", url, params=params, symbol=clean)
    bars = payload.get("bars") or []
    if not bars:
        raise NoMarketDataError(clean, clean, f"Alpaca returned no bars for {clean}")

    rows = []
    for bar in bars:
        ts = bar.get("t") or bar.get("timestamp")
        rows.append(
            {
                "Date": ts,
                "Open": bar.get("o") or bar.get("open"),
                "High": bar.get("h") or bar.get("high"),
                "Low": bar.get("l") or bar.get("low"),
                "Close": bar.get("c") or bar.get("close"),
                "Volume": bar.get("v") or bar.get("volume"),
            }
        )

    frame = pd.DataFrame(rows)
    frame["Date"] = pd.to_datetime(frame["Date"], errors="coerce", utc=True)
    if frame["Date"].dt.tz is not None:
        frame["Date"] = frame["Date"].dt.tz_localize(None)
    frame = frame.dropna(subset=["Date"])
    return _clean_dataframe(frame)


def get_alpaca_stock_data(
    symbol: Annotated[str, "ticker symbol of the company"],
    start_date: Annotated[str, "Start date in yyyy-mm-dd format"],
    end_date: Annotated[str, "End date in yyyy-mm-dd format"],
) -> str:
    datetime.strptime(start_date, "%Y-%m-%d")
    datetime.strptime(end_date, "%Y-%m-%d")

    clean = _normalize_us_symbol(symbol)
    data = _fetch_bars(clean, start_date, end_date)
    _assert_ohlcv_not_stale(data, end_date, clean, f"alpaca:{clean}")

    for col in ("Open", "High", "Low", "Close"):
        if col in data.columns:
            data[col] = data[col].round(2)

    cfg = _settings()
    header = (
        f"# Stock data for {clean} (live via Alpaca {cfg['profile']}) "
        f"from {start_date} to {end_date}\n"
        f"# Total records: {len(data)}\n"
        f"# Data retrieved on: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
        f"# Feed: {cfg['feed']}\n"
    )

    quote = fetch_alpaca_quote(clean)
    if quote and quote.get("ltp") is not None:
        header += (
            f"# Live quote: LTP={quote.get('ltp')} bid={quote.get('bid')} "
            f"ask={quote.get('ask')}\n"
        )
    else:
        snap = fetch_alpaca_trade_snapshot(clean)
        if snap and snap.get("ltp") is not None:
            header += f"# Live trade: LTP={snap.get('ltp')}\n"
    header += "\n"

    return header + data.to_csv(index=False)


def _load_alpaca_ohlcv(symbol: str, curr_date: str) -> pd.DataFrame:
    curr_dt = pd.to_datetime(curr_date)
    start = (curr_dt - relativedelta(years=5)).strftime("%Y-%m-%d")
    end = curr_date
    data = _fetch_bars(symbol, start, end)
    data = data[data["Date"] <= curr_dt]
    clean = _normalize_us_symbol(symbol)
    _assert_ohlcv_not_stale(data, curr_date, clean, f"alpaca:{clean}")
    return data


def get_alpaca_indicators(
    symbol: Annotated[str, "ticker symbol of the company"],
    indicator: Annotated[str, "technical indicator to get the analysis and report of"],
    curr_date: Annotated[str, "The current trading date you are trading on, YYYY-mm-dd"],
    look_back_days: Annotated[int, "how many days to look back"],
) -> str:
    from tradingagents.dataflows import stockstats_utils

    original_loader = stockstats_utils.load_ohlcv
    try:
        stockstats_utils.load_ohlcv = _load_alpaca_ohlcv
        return get_stock_stats_indicators_window(symbol, indicator, curr_date, look_back_days)
    finally:
        stockstats_utils.load_ohlcv = original_loader
