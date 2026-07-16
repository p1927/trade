"""Nifty OHLCV-derived technical features for index prediction."""

from __future__ import annotations

import numpy as np
import pandas as pd


def compute_rsi(close: pd.Series, period: int = 14) -> pd.Series:
    """Wilder-style RSI on close prices."""
    delta = close.astype(float).diff()
    gain = delta.clip(lower=0.0)
    loss = (-delta).clip(lower=0.0)
    avg_gain = gain.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    rsi = 100.0 - (100.0 / (1.0 + rs))
    return rsi.fillna(50.0)


def compute_return_pct(close: pd.Series, days: int) -> pd.Series:
    """Backward-looking return (%) over ``days``."""
    shifted = close.astype(float).shift(days)
    return (close.astype(float) - shifted) / shifted.replace(0, np.nan) * 100.0


def compute_realized_vol(close: pd.Series, window: int = 20) -> pd.Series:
    """Annualized realized vol (%) from daily log returns."""
    log_ret = np.log(close.astype(float) / close.astype(float).shift(1))
    daily_std = log_ret.rolling(window=window, min_periods=max(3, window // 2)).std()
    return daily_std * np.sqrt(252.0) * 100.0


def compute_ma_distance_pct(close: pd.Series, window: int = 20) -> pd.Series:
    """Distance (%) of close from simple moving average."""
    ma = close.astype(float).rolling(window=window, min_periods=max(3, window // 2)).mean()
    return (close.astype(float) - ma) / ma.replace(0, np.nan) * 100.0


def enrich_nifty_technical_columns(frame: pd.DataFrame) -> pd.DataFrame:
    """Add technical feature columns from a ``date`` + ``close`` history frame."""
    if frame.empty or "close" not in frame.columns:
        return frame

    out = frame.copy()
    close = out["close"].astype(float)
    out["nifty_return_7d"] = compute_return_pct(close, 7)
    out["nifty_return_14d"] = compute_return_pct(close, 14)
    out["nifty_rsi_14"] = compute_rsi(close, 14)
    out["nifty_realized_vol_20d"] = compute_realized_vol(close, 20)
    out["nifty_ma20_distance_pct"] = compute_ma_distance_pct(close, 20)
    return out


def latest_technical_factor_dict(frame: pd.DataFrame) -> dict[str, float]:
    """Return the most recent row's technical features as a flat dict."""
    enriched = enrich_nifty_technical_columns(frame)
    if enriched.empty:
        return {}

    keys = (
        "nifty_return_7d",
        "nifty_return_14d",
        "nifty_rsi_14",
        "nifty_realized_vol_20d",
        "nifty_ma20_distance_pct",
    )
    row = enriched.iloc[-1]
    out: dict[str, float] = {}
    for key in keys:
        if key not in row.index:
            continue
        value = row[key]
        if value is None or (isinstance(value, float) and np.isnan(value)):
            continue
        out[key] = float(value)
    return out


def technical_factor_rows(frame: pd.DataFrame) -> list[dict]:
    """Build factor rows from the latest technical snapshot."""
    latest = latest_technical_factor_dict(frame)
    return [
        {"factor": name, "value": value, "source": "nifty_technical"}
        for name, value in latest.items()
    ]
