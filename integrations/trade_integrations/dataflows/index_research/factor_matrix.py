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
    "nifty_ma50_distance_pct",
    "nifty_ma200_distance_pct",
    "nifty_macd_line",
    "nifty_macd_signal",
    "nifty_macd_histogram",
    "nifty_bb_percent_b",
    "nifty_bb_width_pct",
    "nifty_stoch_k",
    "nifty_stoch_d",
    "nifty_williams_r",
    "nifty_cci_20",
    "nifty_adx_14",
    "nifty_atr_pct",
    "nifty_golden_cross_signal",
    "qfinindia_skew",
    "qfinindia_expected_move",
    "qfinindia_tail_risk",
    "constituent_momentum_7d",
    "days_to_monthly_expiry",
    "is_budget_week",
    "is_results_season",
    "institutional_net_5d",
    "dii_absorption_ratio",
)

_MAX_FEATURES = 40
_MIN_ABS_CORR = 0.05

# Always include in Ridge training when present in history (flows, vol, oil).
_PINNED_MACRO_FACTORS: frozenset[str] = frozenset(
    {
        "fii_net_5d",
        "dii_net_5d",
        "oil_brent",
        "india_vix",
        "nifty_pcr",
        "institutional_net_5d",
        "dii_absorption_ratio",
    }
)


def _forward_return_pct(close: pd.Series, horizon_days: int) -> pd.Series:
    future = close.shift(-horizon_days)
    return (future - close) / close * 100.0


_EXCLUDED_REDUNDANT: frozenset[str] = frozenset({"sector_breadth_mean_sentiment"})


def _select_macro_columns(
    history_df: pd.DataFrame,
    horizon: HorizonProfile | None = None,
) -> list[str]:
    from trade_integrations.dataflows.index_research.horizon_features import (
        extended_macro_keys_for_horizon,
    )

    if horizon is not None:
        preferred = list(extended_macro_keys_for_horizon(horizon))
        ordered = preferred + [key for key in MACRO_FACTOR_KEYS if key not in preferred]
    else:
        ordered = list(MACRO_FACTOR_KEYS)

    present = [
        key for key in ordered if key in history_df.columns and key not in _EXCLUDED_REDUNDANT
    ]
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

    macro_cols = _select_macro_columns(frame, horizon)
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

    for col in _PINNED_MACRO_FACTORS:
        if col in macro_cols and col not in selected:
            selected.append(col)

    X = usable[selected].to_numpy(dtype=float)
    return X, y, selected
