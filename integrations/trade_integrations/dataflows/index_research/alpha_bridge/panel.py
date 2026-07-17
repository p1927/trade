"""Build Alpha Zoo wide OHLCV panel for Nifty 50 constituents."""

from __future__ import annotations

import logging
from typing import Any

import pandas as pd

from trade_integrations.dataflows.index_research.alpha_bridge.config import lookback_days
from trade_integrations.dataflows.index_research.constituents import load_nifty50_constituents

logger = logging.getLogger(__name__)

_NIFTY50_FALLBACK = (
    "RELIANCE", "TCS", "HDFCBANK", "ICICIBANK", "INFY", "ITC", "SBIN", "BHARTIARTL",
    "KOTAKBANK", "LT", "HINDUNILVR", "AXISBANK", "BAJFINANCE", "ASIANPAINT", "MARUTI",
    "SUNPHARMA", "TITAN", "ULTRACEMCO", "NESTLEIND", "WIPRO", "HCLTECH", "POWERGRID",
    "NTPC", "TATAMOTORS", "M&M", "ADANIENT", "JSWSTEEL", "TATASTEEL", "COALINDIA",
    "ONGC", "GRASIM", "TECHM", "HDFCLIFE", "SBILIFE", "BAJAJFINSV", "INDUSINDBK",
    "CIPLA", "DRREDDY", "APOLLOHOSP", "EICHERMOT", "HEROMOTOCO", "BRITANNIA",
    "DIVISLAB", "TATACONSUM", "BPCL", "HINDALCO", "ADANIPORTS", "LTIM", "BEL",
)


def _constituent_symbols() -> list[str]:
    rows = load_nifty50_constituents()
    symbols = [row.symbol.upper().strip() for row in rows if row.symbol]
    if symbols:
        return symbols
    logger.warning("alpha_bridge: using Nifty 50 fallback symbol list")
    return list(_NIFTY50_FALLBACK)


def _wide_from_long(
    fetched: dict[str, pd.DataFrame],
) -> dict[str, pd.DataFrame]:
    if not fetched:
        return {}
    all_dates = sorted(set().union(*(df.index for df in fetched.values())))
    if not all_dates:
        return {}
    all_codes = sorted(fetched.keys())
    date_index = pd.DatetimeIndex(all_dates)
    panel: dict[str, pd.DataFrame] = {}
    for field in ("open", "high", "low", "close", "volume"):
        present = {
            code: df[field] for code, df in fetched.items() if field in df.columns
        }
        if not present:
            continue
        wide = pd.concat(present, axis=1)
        wide = wide.reindex(index=date_index, columns=all_codes)
        panel[field] = wide.astype(float)
    if all(k in panel for k in ("open", "high", "low", "close")):
        panel["vwap"] = (
            panel["open"] + panel["high"] + panel["low"] + panel["close"]
        ) / 4.0
    return panel


def build_nifty50_panel(
    *,
    as_of_day: str | None = None,
    lookback: int | None = None,
) -> dict[str, pd.DataFrame]:
    """Return Alpha Zoo panel dict for Nifty 50 (wide OHLCV + vwap)."""
    from trade_integrations.dataflows.index_research.alpha_bridge.india_ohlcv import (
        load_symbols_ohlcv,
    )

    end = (as_of_day or pd.Timestamp.today().date().isoformat())[:10]
    days = lookback if lookback is not None else lookback_days()
    start = (pd.Timestamp(end) - pd.Timedelta(days=days)).date().isoformat()

    fetched: dict[str, pd.DataFrame] = {}
    for code, frame in load_symbols_ohlcv(
        _constituent_symbols(), start_date=start, end_date=end
    ).items():
        if frame is None or frame.empty or "close" not in frame.columns:
            continue
        df = frame.copy()
        df["date"] = pd.to_datetime(df["date"])
        df = df.set_index("date").sort_index()
        mask = df.index <= pd.Timestamp(end)
        df = df.loc[mask]
        for col in ("open", "high", "low", "close", "volume"):
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce")
        keep = [c for c in ("open", "high", "low", "close", "volume") if c in df.columns]
        trimmed = df[keep].dropna(subset=["close"])
        if not trimmed.empty:
            fetched[code] = trimmed

    panel = _wide_from_long(fetched)
    panel["_meta"] = {
        "universe": "nifty50",
        "as_of": end,
        "constituent_count": len(fetched),
    }
    return panel


def panel_metadata(panel: dict[str, pd.DataFrame]) -> dict[str, Any]:
    meta = panel.get("_meta")
    return meta if isinstance(meta, dict) else {}
