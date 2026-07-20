"""India equity OHLCV loader for alpha_bridge (hub cache → OpenAlgo → yfinance)."""

from __future__ import annotations

import logging
import time
from datetime import date, timedelta

import pandas as pd

logger = logging.getLogger(__name__)


def _normalize_nse_symbol(symbol: str) -> str | None:
    """Map aliases and skip symbols with no India OHLCV source."""
    from trade_integrations.openalgo.symbols import _INDMONEY_UNAVAILABLE, _OPENALGO_ALIASES

    base = symbol.strip().upper().replace(".NS", "").replace(".BO", "")
    if not base or base in _INDMONEY_UNAVAILABLE:
        return None
    if base in _OPENALGO_ALIASES:
        base = _OPENALGO_ALIASES[base][0]
    return base


def _normalize_history_frame(raw: pd.DataFrame) -> pd.DataFrame:
    if raw is None or raw.empty:
        return pd.DataFrame(columns=["date", "close"])

    frame = raw.copy()
    rename = {
        "Date": "date",
        "Datetime": "date",
        "Open": "open",
        "High": "high",
        "Low": "low",
        "Close": "close",
        "Volume": "volume",
    }
    for src, dst in rename.items():
        if src in frame.columns and dst not in frame.columns:
            frame = frame.rename(columns={src: dst})

    if "date" not in frame.columns:
        if isinstance(frame.index, pd.DatetimeIndex):
            frame = frame.reset_index().rename(columns={"index": "date"})
        elif frame.index.name:
            frame = frame.reset_index().rename(columns={frame.index.name: "date"})

    if "date" not in frame.columns or "close" not in frame.columns:
        return pd.DataFrame(columns=["date", "close"])

    out = frame.copy()
    out["date"] = pd.to_datetime(out["date"]).dt.strftime("%Y-%m-%d")
    cols = ["date", "close"]
    for optional in ("open", "high", "low", "volume"):
        if optional in out.columns:
            cols.append(optional)
    return out[cols].dropna(subset=["date", "close"]).sort_values("date").reset_index(drop=True)


def _fetch_yfinance(symbol: str, start_date: str, end_date: str) -> pd.DataFrame:
    import yfinance as yf

    from trade_integrations.dataflows.company_research.market import Market, detect_market, normalize_ticker

    if detect_market(symbol) == Market.US:
        return pd.DataFrame(columns=["date", "close"])

    normalized = normalize_ticker(symbol, market=Market.IN)
    ticker = normalized.yfinance_symbol
    hist = yf.Ticker(ticker).history(start=start_date, end=end_date, auto_adjust=True)
    return _normalize_history_frame(hist.reset_index() if not hist.empty else pd.DataFrame())


def load_symbol_ohlcv(
    symbol: str,
    *,
    start_date: str,
    end_date: str,
) -> pd.DataFrame:
    """Load daily OHLCV for one NSE symbol between start/end (YYYY-MM-DD)."""
    from trade_integrations.dataflows.company_research.market import Market, detect_market
    from trade_integrations.dataflows.openalgo import load_india_ohlcv

    if detect_market(symbol) == Market.US:
        return pd.DataFrame(columns=["date", "close"])

    start = start_date[:10]
    end = end_date[:10]
    try:
        days = max(1, (date.fromisoformat(end) - date.fromisoformat(start)).days + 1)
    except ValueError:
        days = 365
    frame = load_india_ohlcv(symbol, days=days, start_date=start, end_date=end)
    if frame.empty:
        return _fetch_yfinance(symbol, start, end)
    if "date" in frame.columns and "Date" not in frame.columns:
        working = frame.rename(
            columns={
                "date": "Date",
                "open": "Open",
                "high": "High",
                "low": "Low",
                "close": "Close",
                "volume": "Volume",
            }
        )
        return _normalize_history_frame(working)
    return _normalize_history_frame(frame)


def load_symbols_ohlcv(
    symbols: list[str],
    *,
    start_date: str,
    end_date: str,
) -> dict[str, pd.DataFrame]:
    from trade_integrations.env import ensure_vibe_stack_heal

    ensure_vibe_stack_heal()

    out: dict[str, pd.DataFrame] = {}
    for idx, symbol in enumerate(symbols):
        base = _normalize_nse_symbol(symbol)
        if not base:
            continue
        frame = load_symbol_ohlcv(base, start_date=start_date, end_date=end_date)
        if not frame.empty:
            out[base] = frame
        if idx + 1 < len(symbols):
            time.sleep(0.05)
    return out
