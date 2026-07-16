"""Quantitative price-band forecast for single-name equities."""

from __future__ import annotations

import math
from typing import Any

import numpy as np
import pandas as pd

from trade_integrations.dataflows.index_research.technical_features import (
    compute_realized_vol,
    compute_return_pct,
)


def _classify_view(expected_return_pct: float) -> str:
    if expected_return_pct >= 0.75:
        return "bullish"
    if expected_return_pct <= -0.75:
        return "bearish"
    return "neutral"


def _fetch_history(ticker: str, *, days: int = 120) -> pd.DataFrame:
    import yfinance as yf

    sym = ticker.strip().upper().replace(".NS", "").replace(".BO", "")
    yf_sym = sym if sym.endswith((".NS", ".BO")) else f"{sym}.NS"
    raw = yf.download(yf_sym, period=f"{days}d", interval="1d", progress=False, auto_adjust=True)
    if raw is None or raw.empty:
        return pd.DataFrame()
    frame = raw.reset_index()
    if isinstance(frame.columns, pd.MultiIndex):
        frame.columns = [str(c[0]).lower() if isinstance(c, tuple) else str(c).lower() for c in frame.columns]
    else:
        frame.columns = [str(c).lower() for c in frame.columns]
    if "close" not in frame.columns and "adj close" in frame.columns:
        frame["close"] = frame["adj close"]
    return frame[["date", "close"]].dropna() if "close" in frame.columns else pd.DataFrame()


def predict_stock(
    ticker: str,
    spot: float,
    *,
    horizon_days: int = 14,
    history: pd.DataFrame | None = None,
    earnings_widen: bool = False,
) -> dict[str, Any]:
    """
    Return model forecast: expected return %, range band, view, model_confidence.

    Uses realized volatility and recent momentum; no paid forecast vendors.
    """
    if spot <= 0:
        return {
            "view": "neutral",
            "expected_return_pct": 0.0,
            "range": {"low": None, "high": None},
            "horizon_days": horizon_days,
            "model_confidence": 0.0,
            "volatility_annual_pct": None,
        }

    frame = history if history is not None else _fetch_history(ticker)
    if frame.empty or len(frame) < 10:
        # Fallback: 2% band per week scaled by horizon
        move_pct = 2.0 * math.sqrt(max(horizon_days, 1) / 5.0)
        return {
            "view": "neutral",
            "expected_return_pct": 0.0,
            "range": {
                "low": round(spot * (1 - move_pct / 100), 2),
                "high": round(spot * (1 + move_pct / 100), 2),
            },
            "horizon_days": horizon_days,
            "model_confidence": 0.35,
            "volatility_annual_pct": None,
            "source": "fallback_band",
        }

    close = frame["close"].astype(float)
    vol_series = compute_realized_vol(close, window=20)
    vol_pct = float(vol_series.iloc[-1]) if len(vol_series) else 20.0
    if math.isnan(vol_pct) or vol_pct <= 0:
        vol_pct = 20.0

    ret_5d = float(compute_return_pct(close, 5).iloc[-1]) if len(close) > 5 else 0.0
    if math.isnan(ret_5d):
        ret_5d = 0.0

    # Scale daily vol to horizon (sqrt time)
    horizon_factor = math.sqrt(max(horizon_days, 1) / 252.0)
    band_pct = vol_pct * horizon_factor
    if earnings_widen:
        band_pct *= 1.25

    momentum_tilt = max(-band_pct * 0.5, min(band_pct * 0.5, ret_5d * 0.3))
    expected_return_pct = round(momentum_tilt, 3)

    half_band = band_pct / 2.0
    center = spot * (1 + expected_return_pct / 100.0)
    low = round(center * (1 - half_band / 100.0), 2)
    high = round(center * (1 + half_band / 100.0), 2)

    data_points = len(frame)
    model_confidence = round(min(0.85, 0.4 + data_points / 200.0), 3)

    return {
        "view": _classify_view(expected_return_pct),
        "expected_return_pct": expected_return_pct,
        "range": {"low": low, "high": high},
        "horizon_days": horizon_days,
        "model_confidence": model_confidence,
        "volatility_annual_pct": round(vol_pct, 2),
        "momentum_5d_pct": round(ret_5d, 3),
        "source": "realized_vol_momentum",
    }
