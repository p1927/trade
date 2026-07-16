"""Nifty OHLCV history and aligned factor time-series for model training."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pandas as pd

from trade_integrations.dataflows.index_research.factor_store import load_factor_history

NIFTY_SYMBOL = "^NSEI"


def load_nifty_history(days: int = 365) -> pd.DataFrame:
    """Load Nifty spot close history via yfinance ``^NSEI``."""
    import yfinance as yf

    end = datetime.now(timezone.utc)
    start = end - timedelta(days=days)
    hist = yf.Ticker(NIFTY_SYMBOL).history(start=start, end=end)
    if hist.empty:
        return pd.DataFrame(columns=["date", "close"])

    frame = hist.reset_index()
    if "Date" in frame.columns:
        date_series = frame["Date"]
    elif "Datetime" in frame.columns:
        date_series = frame["Datetime"]
    elif "index" in frame.columns:
        date_series = frame["index"]
    else:
        date_series = frame.iloc[:, 0]
    frame["date"] = pd.to_datetime(date_series).dt.strftime("%Y-%m-%d")
    if "Close" in frame.columns:
        frame["close"] = frame["Close"].astype(float)
    elif "close" in frame.columns:
        frame["close"] = frame["close"].astype(float)
    else:
        return pd.DataFrame(columns=["date", "close"])
    return frame[["date", "close"]].sort_values("date").reset_index(drop=True)


def load_aligned_factor_history(days: int = 365) -> pd.DataFrame:
    """Merge Nifty closes with wide-format daily factor columns from the factor store."""
    nifty = load_nifty_history(days=days)
    if nifty.empty:
        return pd.DataFrame()

    start = nifty["date"].iloc[0]
    end = nifty["date"].iloc[-1]
    factors_long = load_factor_history(start, end)
    if factors_long.empty:
        return nifty

    if "date" not in factors_long.columns or "factor" not in factors_long.columns:
        return nifty

    value_col = "value" if "value" in factors_long.columns else factors_long.columns[-1]
    wide = factors_long.pivot_table(
        index="date",
        columns="factor",
        values=value_col,
        aggfunc="last",
    )
    wide = wide.reset_index()
    wide["date"] = wide["date"].astype(str).str[:10]

    aligned = nifty.merge(wide, on="date", how="left")
    return aligned.sort_values("date").reset_index(drop=True)
