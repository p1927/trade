"""Build sklearn-ready feature matrix from aligned Nifty + factor history."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

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
    # Phase I — included when coverage gate passes (see phase_i_coverage.py)
    "nifty_earnings_yield",
    "equity_risk_premium",
    "india_term_spread",
    "india_credit_spread",
    "india_vix_velocity_3d",
    "usd_inr_momentum_5d",
    "us_10y_velocity_3d",
    "fii_net_5d_momentum",
)

from trade_integrations.dataflows.index_research.news_event_features import NEWS_EVENT_FACTOR_KEYS

NEWS_EVENT_MACRO_KEYS: tuple[str, ...] = NEWS_EVENT_FACTOR_KEYS

_MAX_FEATURES = 40
_MIN_ABS_CORR = 0.05
_MIN_COLUMN_COVERAGE_RATIO = 0.45
_MIN_COLUMN_COVERAGE_ROWS = 10
_MIN_FEATURE_STD = 1e-12
_HIGH_CORR_PAIR_THRESHOLD = 0.85

# Always include in Ridge training when present in history (flows, vol, oil).
# Keep FII + DII legs separately (distinct foreign vs domestic signals); omit
# institutional_net_5d because it is algebraically FII + DII.
_PINNED_MACRO_FACTORS: frozenset[str] = frozenset(
    {
        "fii_net_5d",
        "dii_net_5d",
        "oil_brent",
        "india_vix",
        "nifty_pcr",
        "dii_absorption_ratio",
    }
)


def _forward_return_pct(close: pd.Series, horizon_days: int) -> pd.Series:
    future = close.shift(-horizon_days)
    return (future - close) / close * 100.0


_EXCLUDED_REDUNDANT: frozenset[str] = frozenset(
    {
        "sector_breadth_mean_sentiment",
        "oil_wti",
        "constituent_momentum_7d",
        "alpha_zoo_ls_spread",
        "alpha_zoo_breadth",
        "alpha_zoo_momentum_consensus",
        "alpha_zoo_dispersion",
    }
)

# When both appear in candidate columns, drop the second (keep interpretable primary).
_REDUNDANCY_PAIRS: tuple[tuple[str, str], ...] = (
    ("nifty_return_7d", "constituent_momentum_7d"),
    ("oil_brent", "oil_wti"),
    ("nifty_return_7d", "nifty_return_14d"),  # prefer shorter horizon when both correlate
    ("nifty_earnings_yield", "nifty_pe"),
    ("nifty_book_to_market", "nifty_pb"),
    # FII + DII are distinct flow drivers; combined net is their sum (MDPI 2026, NiftyPulse).
    ("fii_net_5d", "institutional_net_5d"),
)

# Within each group keep the first present member; drop the rest (TA multicollinearity guidance).
_REDUNDANCY_GROUPS: tuple[tuple[str, ...], ...] = (
    # India G-Sec curve: term spread (10Y − T-Bill) subsumes raw yield levels.
    # Corporate credit spread is handled separately when observed (see _apply_redundancy_prune).
    ("india_term_spread", "india_10y", "india_91d_tbill"),
    # Oscillators: stochastic %K; Williams %R is a linear transform (StockSharp / TA-Lib studies).
    ("nifty_stoch_k", "nifty_williams_r", "nifty_stoch_d", "nifty_bb_percent_b"),
    # MACD: histogram is line − signal; keep the actionable residual.
    ("nifty_macd_histogram", "nifty_macd_line", "nifty_macd_signal"),
    # Valuation stretch: equity risk premium over long MA distance.
    ("equity_risk_premium", "nifty_ma200_distance_pct"),
    ("nifty_book_to_market", "nifty_pb_zscore_5y"),
)


def _safe_abs_corr(series: pd.Series, target: pd.Series) -> float | None:
    """Pearson |r| with guards for zero-variance columns (avoids numpy divide warnings)."""
    aligned = pd.concat([series, target], axis=1).dropna()
    if len(aligned) < 3:
        return None
    a = aligned.iloc[:, 0].astype(float)
    b = aligned.iloc[:, 1].astype(float)
    if float(a.std(ddof=0)) < _MIN_FEATURE_STD or float(b.std(ddof=0)) < _MIN_FEATURE_STD:
        return None
    corr = a.corr(b)
    if corr is None or np.isnan(corr):
        return None
    return abs(float(corr))


def _credit_spread_observed_for_frame(history_df: pd.DataFrame) -> bool:
    try:
        from trade_integrations.dataflows.index_research.spread_features import india_credit_spread_is_observed

        return india_credit_spread_is_observed(history_df)
    except Exception:
        return False


def _prune_high_correlation_pairs(
    columns: list[str],
    usable: pd.DataFrame,
) -> list[str]:
    """Drop one member of pairs with |r| > threshold not already pruned by redundancy rules."""
    if len(columns) < 2:
        return columns
    present = list(columns)
    drop: set[str] = set()
    for i, col_a in enumerate(present):
        if col_a in drop:
            continue
        for col_b in present[i + 1 :]:
            if col_b in drop:
                continue
            corr = _safe_abs_corr(usable[col_a], usable[col_b])
            if corr is None or corr < _HIGH_CORR_PAIR_THRESHOLD:
                continue
            # Keep the factor with stronger |corr| to forward return when available.
            target = usable["target"] if "target" in usable.columns else None
            if target is not None:
                corr_a = _safe_abs_corr(usable[col_a], target) or 0.0
                corr_b = _safe_abs_corr(usable[col_b], target) or 0.0
                drop.add(col_b if corr_a >= corr_b else col_a)
            else:
                drop.add(col_b)
    return [col for col in present if col not in drop]


def _apply_redundancy_prune(
    columns: list[str],
    *,
    credit_spread_observed: bool = False,
) -> list[str]:
    """Drop redundant pair/group members already excluded or superseded."""
    present = set(columns)
    drop: set[str] = set()
    for keep, discard in _REDUNDANCY_PAIRS:
        if keep in present and discard in present:
            drop.add(discard)
    for group in _REDUNDANCY_GROUPS:
        keep = next((name for name in group if name in present), None)
        if keep is None:
            continue
        drop |= {name for name in group if name != keep and name in present}
    drop |= _EXCLUDED_REDUNDANT & present
    result = [c for c in columns if c not in drop]
    # Term spread = yield-curve slope; credit spread = corporate vs G-sec risk.
    # Keep both only when credit is observed — proxy is an affine function of term spread.
    if not credit_spread_observed:
        present_after = set(result)
        if "india_term_spread" in present_after and "india_credit_spread" in present_after:
            result = [c for c in result if c != "india_credit_spread"]
    return result


def redundancy_audit() -> dict[str, Any]:
    """Document factors excluded from Ridge training for interpretability."""
    return {
        "excluded_redundant": sorted(_EXCLUDED_REDUNDANT),
        "redundancy_pairs": [list(pair) for pair in _REDUNDANCY_PAIRS],
        "redundancy_groups": [list(group) for group in _REDUNDANCY_GROUPS],
    }


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

    try:
        from trade_integrations.dataflows.index_research.news_event_features import is_news_ridge_enabled

        if is_news_ridge_enabled():
            ordered = list(ordered) + [k for k in NEWS_EVENT_MACRO_KEYS if k not in ordered]
    except Exception:
        pass

    present = [
        key for key in ordered if key in history_df.columns and key not in _EXCLUDED_REDUNDANT
    ]
    credit_observed = _credit_spread_observed_for_frame(history_df)
    present = _apply_redundancy_prune(present, credit_spread_observed=credit_observed)
    try:
        from trade_integrations.dataflows.index_research.sector_promotion import (
            promoted_sector_factor_keys,
        )

        for key in promoted_sector_factor_keys():
            if key in history_df.columns and key not in present:
                present.append(key)
        from trade_integrations.dataflows.index_research.event_promotion import (
            promoted_event_factor_keys,
        )

        for key in promoted_event_factor_keys():
            if key in history_df.columns and key not in present:
                present.append(key)
        from trade_integrations.dataflows.index_research.alpha_bridge.promotion import (
            promoted_alpha_zoo_factor_keys,
        )

        for key in promoted_alpha_zoo_factor_keys():
            if key in history_df.columns and key not in present:
                present.append(key)
    except ImportError:
        pass
    if present:
        return present
    exclude = {"date", "close", "open", "high", "low", "volume"}
    try:
        from trade_integrations.dataflows.index_research.alpha_bridge.promotion import (
            ALPHA_ZOO_FACTOR_KEYS,
            promoted_alpha_zoo_factor_keys,
        )

        promoted_alpha = set(promoted_alpha_zoo_factor_keys())
    except ImportError:
        promoted_alpha = set()
        ALPHA_ZOO_FACTOR_KEYS = ()
    return [
        col
        for col in history_df.columns
        if col not in exclude
        and pd.api.types.is_numeric_dtype(history_df[col])
        and (col not in ALPHA_ZOO_FACTOR_KEYS or col in promoted_alpha)
    ]


def build_factor_matrix(
    history_df: pd.DataFrame,
    horizon: HorizonProfile,
    *,
    force_include_keys: tuple[str, ...] | None = None,
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
    try:
        from trade_integrations.dataflows.index_research.phase_i_coverage import phase_i_keys_for_ridge

        for key in phase_i_keys_for_ridge(frame):
            if key in frame.columns and key not in macro_cols:
                macro_cols.append(key)
    except Exception:
        pass
    force_keys = tuple(force_include_keys or ())
    for col in force_keys:
        if col in frame.columns and col not in macro_cols and col not in {"date", "close", "open", "high", "low", "volume"}:
            macro_cols.append(col)
    if not macro_cols:
        return np.empty((0, 0)), np.empty(0), []

    window = max(1, horizon.feature_window)
    for col in macro_cols:
        frame[col] = pd.to_numeric(frame[col], errors="coerce")
        frame[col] = frame[col].rolling(window=window, min_periods=1).mean()

    min_cov = max(_MIN_COLUMN_COVERAGE_ROWS, int(len(frame) * _MIN_COLUMN_COVERAGE_RATIO))
    macro_cols = [col for col in macro_cols if int(frame[col].notna().sum()) >= min_cov]
    if not macro_cols:
        return np.empty((0, 0)), np.empty(0), []

    usable = frame.dropna(subset=["target"]).copy()
    if len(usable) < 3:
        return np.empty((0, 0)), np.empty(0), []

    y_all = usable["target"]
    selected: list[str] = []
    for col in macro_cols:
        series = usable[col]
        if series.notna().sum() < 3 or float(series.std(ddof=0, skipna=True)) < _MIN_FEATURE_STD:
            continue
        corr = _safe_abs_corr(series, y_all)
        if corr is None or corr < _MIN_ABS_CORR:
            continue
        selected.append(col)

    if not selected:
        selected = macro_cols[: min(len(macro_cols), _MAX_FEATURES)]
    else:
        ranked = sorted(
            selected,
            key=lambda name: _safe_abs_corr(usable[name], y_all) or 0.0,
            reverse=True,
        )
        selected = ranked[:_MAX_FEATURES]

    for col in _PINNED_MACRO_FACTORS:
        if col in macro_cols and col not in selected and col not in _EXCLUDED_REDUNDANT:
            selected.append(col)

    credit_observed = _credit_spread_observed_for_frame(frame)
    selected = _apply_redundancy_prune(selected, credit_spread_observed=credit_observed)

    for col in force_keys:
        if col in frame.columns and col not in selected:
            selected.append(col)

    selected = _apply_redundancy_prune(selected, credit_spread_observed=credit_observed)
    selected = _prune_high_correlation_pairs(selected, usable)

    final = usable.dropna(subset=["target"] + selected).copy()
    if len(final) < 3:
        return np.empty((0, 0)), np.empty(0), []

    y = final["target"].to_numpy(dtype=float)
    X = final[selected].to_numpy(dtype=float)
    return X, y, selected
