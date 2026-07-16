"""Nifty OHLCV history and aligned factor time-series for model training."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import numpy as np
import pandas as pd

from trade_integrations.dataflows.index_research.calendar_features import (
    calendar_factor_dict,
)
from trade_integrations.dataflows.index_research.factor_store import load_factor_history
from trade_integrations.dataflows.index_research.technical_features import (
    enrich_nifty_technical_columns,
)

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


def _append_calendar_columns(frame: pd.DataFrame) -> pd.DataFrame:
    if frame.empty or "date" not in frame.columns:
        return frame
    out = frame.copy()
    for key in ("days_to_monthly_expiry", "is_budget_week", "is_results_season"):
        out[key] = np.nan
    for idx, raw_date in enumerate(out["date"]):
        try:
            as_of = date.fromisoformat(str(raw_date)[:10])
        except ValueError:
            continue
        cal = calendar_factor_dict(as_of)
        for key, value in cal.items():
            out.at[idx, key] = value
    return out


def enrich_history_features(frame: pd.DataFrame) -> pd.DataFrame:
    """Add technical + calendar columns derived from Nifty close history."""
    if frame.empty:
        return frame
    enriched = enrich_nifty_technical_columns(frame)
    return _append_calendar_columns(enriched)


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
    aligned = enrich_history_features(aligned)
    return aligned.sort_values("date").reset_index(drop=True)
