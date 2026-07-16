"""Build sklearn-ready feature matrix from aligned Nifty + factor history."""

from __future__ import annotations

from typing import TYPE_CHECKING

import numpy as np
import pandas as pd

if TYPE_CHECKING:
    from trade_integrations.dataflows.index_research.horizon import HorizonProfile

MACRO_FACTOR_KEYS: tuple[str, ...] = (
    "oil_brent",
    "oil_wti",
    "usd_inr",
    "gold",
    "sp500",
    "us_10y",
    "india_vix",
    "fii_net_5d",
    "dii_net_5d",
    "fii_fut_long_short_ratio",
    "nifty_pe",
    "cpi_yoy_proxy",
    "repo_rate",
    "index_sentiment",
    "nifty_pcr",
    "nifty_return_7d",
    "nifty_return_14d",
    "nifty_rsi_14",
    "nifty_realized_vol_20d",
    "nifty_ma20_distance_pct",
    "constituent_momentum_7d",
    "days_to_monthly_expiry",
    "is_budget_week",
    "is_results_season",
)

_MAX_FEATURES = 40
_MIN_ABS_CORR = 0.05


def _forward_return_pct(close: pd.Series, horizon_days: int) -> pd.Series:
    future = close.shift(-horizon_days)
    return (future - close) / close * 100.0


def _select_macro_columns(history_df: pd.DataFrame) -> list[str]:
    present = [key for key in MACRO_FACTOR_KEYS if key in history_df.columns]
    if present:
        return present
    exclude = {"date", "close", "open", "high", "low", "volume"}
    return [
        col
        for col in history_df.columns
        if col not in exclude and pd.api.types.is_numeric_dtype(history_df[col])
    ]


def build_factor_matrix(
    history_df: pd.DataFrame,
    horizon: HorizonProfile,
) -> tuple[np.ndarray, np.ndarray, list[str]]:
    """Return ``(X, y, feature_names)`` for macro Ridge training.

    ``y`` is the forward Nifty return (%) over ``horizon.days``.
    Features are rolling-smoothed macro columns filtered by |corr| to target.
    """
    if history_df.empty or "close" not in history_df.columns:
        return np.empty((0, 0)), np.empty(0), []

    frame = history_df.copy()
    frame = frame.sort_values("date").reset_index(drop=True)
    frame["target"] = _forward_return_pct(frame["close"].astype(float), horizon.days)

    macro_cols = _select_macro_columns(frame)
    if not macro_cols:
        return np.empty((0, 0)), np.empty(0), []

    window = max(1, horizon.feature_window)
    for col in macro_cols:
        frame[col] = pd.to_numeric(frame[col], errors="coerce")
        frame[col] = frame[col].rolling(window=window, min_periods=1).mean()

    usable = frame.dropna(subset=["target"] + macro_cols).copy()
    if len(usable) < 3:
        return np.empty((0, 0)), np.empty(0), []

    y = usable["target"].to_numpy(dtype=float)
    selected: list[str] = []
    for col in macro_cols:
        series = usable[col]
        if series.std(ddof=0) == 0:
            continue
        corr = abs(series.corr(pd.Series(y)))
        if corr is None or np.isnan(corr) or corr < _MIN_ABS_CORR:
            continue
        selected.append(col)

    if not selected:
        selected = macro_cols[: min(len(macro_cols), _MAX_FEATURES)]
    else:
        ranked = sorted(
            selected,
            key=lambda name: abs(usable[name].corr(pd.Series(y))),
            reverse=True,
        )
        selected = ranked[:_MAX_FEATURES]

    X = usable[selected].to_numpy(dtype=float)
    return X, y, selected
