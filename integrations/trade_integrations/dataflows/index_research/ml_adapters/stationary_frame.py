"""Convert level series to pct-change stationary columns for ML tracks."""

from __future__ import annotations

import pandas as pd

# Macro level columns converted to weekly-ish pct change (5 trading days).
_STATIONARY_SOURCE_COLS: tuple[str, ...] = (
    "repo_rate",
    "cpi_yoy_proxy",
    "us_10y",
    "india_10y",
    "india_91d_tbill",
    "usd_inr",
    "oil_brent",
    "oil_wti",
    "gold",
    "sp500",
    "india_vix",
    "close",
)

_STATIONARY_SUFFIX = "_pct_5d"


def pct_change_columns(source_cols: tuple[str, ...] | None = None) -> tuple[str, ...]:
    cols = source_cols or _STATIONARY_SOURCE_COLS
    return tuple(f"{c}{_STATIONARY_SUFFIX}" for c in cols if c)


def to_stationary_pct_change(
    frame: pd.DataFrame,
    *,
    source_cols: tuple[str, ...] | None = None,
    periods: int = 5,
) -> pd.DataFrame:
    """Append pct-change columns for macro levels and Nifty close."""
    if frame.empty:
        return frame
    out = frame.copy()
    cols = source_cols or _STATIONARY_SOURCE_COLS
    for col in cols:
        if col not in out.columns:
            continue
        series = pd.to_numeric(out[col], errors="coerce")
        out[f"{col}{_STATIONARY_SUFFIX}"] = series.pct_change(periods=periods, fill_method=None) * 100.0
    return out
