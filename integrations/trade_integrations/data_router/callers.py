"""Caller-facing helpers (TradingAgents, Vibe) built on DataRouter."""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

import pandas as pd

from trade_integrations.data_router.router import data_router_enabled, fetch
from trade_integrations.data_router.types import FetchSpec

logger = logging.getLogger(__name__)


def infer_equity_market(symbol: str) -> str:
    raw = (symbol or "").strip().upper()
    if raw.endswith(".NS") or raw.endswith(".BO") or raw.endswith(".NSE"):
        return "india_equity"
    if raw.startswith("^NSE") or raw in {"NIFTY", "NIFTY50", "^NSEI", "BANKNIFTY"}:
        return "india_equity"
    return "us_equity"


def _frame_to_tradingagents_csv(
    frame: pd.DataFrame,
    *,
    symbol: str,
    start_date: str,
    end_date: str,
    source_id: str | None,
) -> str:
    out = frame.copy()
    rename = {
        "date": "Date",
        "open": "Open",
        "high": "High",
        "low": "Low",
        "close": "Close",
        "volume": "Volume",
    }
    for src, dst in rename.items():
        if src in out.columns and dst not in out.columns:
            out = out.rename(columns={src: dst})
    if "Date" not in out.columns and "date" in out.columns:
        out = out.rename(columns={"date": "Date"})
    out = out.set_index("Date") if "Date" in out.columns else out
    for col in ("Open", "High", "Low", "Close"):
        if col in out.columns:
            out[col] = pd.to_numeric(out[col], errors="coerce").round(2)
    csv_string = out.to_csv()
    label = symbol.upper()
    header = f"# Stock data for {label} from {start_date} to {end_date}\n"
    header += f"# Total records: {len(out)}\n"
    header += f"# Source: {source_id or 'hub'}\n"
    header += f"# Data retrieved on: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"
    return header + csv_string


def fetch_stock_data_via_router(
    symbol: str,
    start_date: str,
    end_date: str,
    *,
    market: str | None = None,
    allow_background: bool = False,
) -> str | None:
    """Return TradingAgents CSV string or None when router disabled/miss."""
    if not data_router_enabled():
        return None
    resolved_market = market or infer_equity_market(symbol)
    spec = FetchSpec(
        domain="ohlcv",
        market=resolved_market,
        symbol=symbol,
        start=start_date,
        end=end_date,
    )
    result = fetch(spec, allow_background=allow_background)
    if result.status != "ok" or result.data is None:
        return None
    frame = result.data if isinstance(result.data, pd.DataFrame) else pd.DataFrame()
    if frame.empty:
        return None
    return _frame_to_tradingagents_csv(
        frame,
        symbol=symbol,
        start_date=start_date,
        end_date=end_date,
        source_id=result.source_id,
    )


def catalog_loader_chain(market: str) -> list[str] | None:
    """Map catalog OHLCV chain to Vibe loader names."""
    try:
        from trade_integrations.data_router.catalog import get_chain

        chain = get_chain("ohlcv", market)
    except Exception:
        return None
    if not chain:
        return None

    loader_map = {
        "openalgo": "india_broker",
        "yahoo": "yahoo",
        "stooq": "stooq",
        "yfinance": "yfinance",
        "tiingo": "tiingo",
        "fmp": "fmp",
        "finnhub": "finnhub",
        "alphavantage": "alphavantage",
        "alpha_vantage": "alphavantage",
        "eod_historical": "yfinance",
    }
    mapped: list[str] = []
    for source_id in chain:
        name = loader_map.get(source_id.strip().lower(), source_id.strip().lower())
        if name not in mapped:
            mapped.append(name)
    return mapped or None


def effective_fallback_chain(market: str, default: list[str]) -> list[str]:
    """Prefer catalog chain when DataRouter is enabled."""
    if not data_router_enabled():
        return default
    catalog = catalog_loader_chain(market)
    if not catalog:
        return default
    tail = [name for name in default if name in ("local", "qveris") and name not in catalog]
    return catalog + tail


def order_fetchers_by_catalog(
    domain: str,
    market: str,
    fetchers: list[tuple[str, Any]],
) -> list[tuple[str, Any]]:
    """Reorder parallel fetchers to match DataRouter catalog chain."""
    if not data_router_enabled():
        return fetchers
    try:
        from trade_integrations.data_router.catalog import get_chain

        chain = get_chain(domain, market)
    except Exception:
        return fetchers
    if not chain:
        return fetchers
    by_name = {name: fn for name, fn in fetchers}
    ordered: list[tuple[str, Any]] = []
    for source_id in chain:
        aliases = {
            "dalal_bse": "dalal_bse",
            "yfinance": "yfinance",
            "tapetide": "tapetide",
            "nifty100_intel": "nifty100_financial_intel",
        }
        name = aliases.get(source_id, source_id)
        if name in by_name:
            ordered.append((name, by_name.pop(name)))
    ordered.extend(by_name.items())
    return ordered
