"""Macro factor lags (1–4 weeks) for delayed policy effects."""

from __future__ import annotations

import pandas as pd

# Trading-day lags approximating 1–4 calendar weeks.
_LAG_WEEKS: tuple[tuple[str, int], ...] = (
    ("1w", 5),
    ("2w", 10),
    ("3w", 15),
    ("4w", 20),
)

_LAG_SOURCE_COLS: tuple[str, ...] = (
    "repo_rate",
    "cpi_yoy_proxy",
    "us_10y",
    "india_10y",
)


def _lag_keys_for_col(col: str) -> tuple[str, ...]:
    return tuple(f"{col}_lag_{label}" for label, _ in _LAG_WEEKS)


def macro_lag_factor_keys(source_cols: tuple[str, ...] | None = None) -> tuple[str, ...]:
    cols = source_cols or _LAG_SOURCE_COLS
    keys: list[str] = []
    for col in cols:
        keys.extend(_lag_keys_for_col(col))
    return tuple(keys)


MACRO_LAG_FACTOR_KEYS: tuple[str, ...] = macro_lag_factor_keys()


def enrich_macro_lag_columns(
    frame: pd.DataFrame,
    *,
    source_cols: tuple[str, ...] | None = None,
) -> pd.DataFrame:
    """Backward-looking lags only — safe for T0 prediction."""
    if frame.empty:
        return frame
    out = frame.copy()
    cols = source_cols or _LAG_SOURCE_COLS
    for col in cols:
        if col not in out.columns:
            continue
        series = pd.to_numeric(out[col], errors="coerce")
        for label, lag in _LAG_WEEKS:
            out[f"{col}_lag_{label}"] = series.shift(lag)
    return out
