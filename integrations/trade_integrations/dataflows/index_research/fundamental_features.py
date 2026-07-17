"""Valuation & fundamental yield features (Phase I)."""

from __future__ import annotations

import numpy as np
import pandas as pd

FUNDAMENTAL_OUTPUT_KEYS: tuple[str, ...] = (
    "nifty_earnings_yield",
    "nifty_dividend_yield",
    "nifty_pb",
    "nifty_book_to_market",
    "nifty_pb_zscore_5y",
    "equity_risk_premium",
)


def compute_earnings_yield_from_pe(pe: pd.Series) -> pd.Series:
    """E/P (%) from trailing P/E."""
    pe_num = pd.to_numeric(pe, errors="coerce")
    return (100.0 / pe_num.replace(0, np.nan)).replace([np.inf, -np.inf], np.nan)


def compute_book_to_market_from_pb(pb: pd.Series) -> pd.Series:
    pb_num = pd.to_numeric(pb, errors="coerce")
    return (1.0 / pb_num.replace(0, np.nan)).replace([np.inf, -np.inf], np.nan)


def compute_pb_zscore_5y(pb: pd.Series, *, window: int = 252 * 5) -> pd.Series:
    """Rolling z-score of P/B vs ~5y trading days."""
    pb_num = pd.to_numeric(pb, errors="coerce")
    roll_mean = pb_num.rolling(window=window, min_periods=max(60, window // 10)).mean()
    roll_std = pb_num.rolling(window=window, min_periods=max(60, window // 10)).std()
    return (pb_num - roll_mean) / roll_std.replace(0, np.nan)


def compute_equity_risk_premium(
    earnings_yield: pd.Series,
    bond_yield: pd.Series,
) -> pd.Series:
    """ERP (%) = earnings yield − long bond yield."""
    ey = pd.to_numeric(earnings_yield, errors="coerce")
    by = pd.to_numeric(bond_yield, errors="coerce")
    return ey - by


def _resolve_bond_yield_column(frame: pd.DataFrame) -> pd.Series:
    if "india_10y" in frame.columns:
        return pd.to_numeric(frame["india_10y"], errors="coerce")
    if "repo_rate" in frame.columns:
        return pd.to_numeric(frame["repo_rate"], errors="coerce")
    return pd.Series(np.nan, index=frame.index)


def enrich_fundamental_columns(frame: pd.DataFrame) -> pd.DataFrame:
    """Add valuation / ERP columns when base inputs exist."""
    if frame.empty:
        return frame
    out = frame.copy()

    if "nifty_pe" in out.columns:
        out["nifty_earnings_yield"] = compute_earnings_yield_from_pe(out["nifty_pe"])

    if "nifty_pb" in out.columns:
        out["nifty_book_to_market"] = compute_book_to_market_from_pb(out["nifty_pb"])
        out["nifty_pb_zscore_5y"] = compute_pb_zscore_5y(out["nifty_pb"])

    if "nifty_dividend_yield" not in out.columns:
        out["nifty_dividend_yield"] = np.nan

    if "nifty_earnings_yield" in out.columns:
        bond = _resolve_bond_yield_column(out)
        out["equity_risk_premium"] = compute_equity_risk_premium(out["nifty_earnings_yield"], bond)

    if "india_10y" in out.columns and "india_91d_tbill" in out.columns:
        out["india_term_spread"] = pd.to_numeric(out["india_10y"], errors="coerce") - pd.to_numeric(
            out["india_91d_tbill"], errors="coerce"
        )

    return out


def fundamental_factor_rows_from_dict(factors: dict) -> list[dict]:
    """Latest-row factor dicts from a flat macro snapshot."""
    frame = pd.DataFrame([dict(factors)])
    enriched = enrich_fundamental_columns(frame)
    row = enriched.iloc[0]
    out: list[dict] = []
    for key in FUNDAMENTAL_OUTPUT_KEYS:
        if key not in enriched.columns:
            continue
        val = row.get(key)
        if val is None or (isinstance(val, float) and np.isnan(val)):
            continue
        out.append({"factor": key, "value": float(val), "source": "fundamental_features"})
    return out


def phase_i_factor_rows_for_history(frame: pd.DataFrame) -> dict[str, pd.Series]:
    """Return date-indexed series for persistence from aligned history."""
    if frame.empty or "date" not in frame.columns:
        return {}
    enriched = enrich_fundamental_columns(frame)
    idx = enriched["date"].astype(str)
    result: dict[str, pd.Series] = {}
    keys = set(FUNDAMENTAL_OUTPUT_KEYS) | {"india_term_spread"}
    for key in keys:
        if key not in enriched.columns:
            continue
        series = pd.to_numeric(enriched[key], errors="coerce")
        result[key] = pd.Series(series.values, index=idx)
    return result
