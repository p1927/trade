"""Spread, velocity, and momentum macro features (Phase I)."""

from __future__ import annotations

import numpy as np
import pandas as pd

from trade_integrations.dataflows.index_research.technical_features import compute_return_pct

SPREAD_OUTPUT_KEYS: tuple[str, ...] = (
    "india_vix_velocity_3d",
    "usd_inr_momentum_5d",
    "us_10y_velocity_3d",
    "fii_net_5d_momentum",
    "india_term_spread",
    "india_credit_spread",
)


def compute_velocity_3d(series: pd.Series) -> pd.Series:
    """3-session percent change."""
    return compute_return_pct(series.astype(float), days=3)


def compute_momentum_5d(series: pd.Series) -> pd.Series:
    """5-session percent change."""
    return compute_return_pct(series.astype(float), days=5)


def compute_level_momentum(series: pd.Series, days: int = 5) -> pd.Series:
    """Absolute change over ``days`` (for flow sums)."""
    return series.astype(float) - series.astype(float).shift(days)


def compute_credit_spread_proxy(term_spread: pd.Series | float) -> pd.Series | float:
    """Documented BAA-AAA proxy when CRISIL series unavailable.

    Maps India G-Sec term spread (10Y − 91D, %) to a corporate credit spread (%):
    baseline 0.45% + 0.18 × term spread, floored at 0.35%.
    """
    if isinstance(term_spread, pd.Series):
        spread = pd.to_numeric(term_spread, errors="coerce")
        return (0.45 + 0.18 * spread).clip(lower=0.35)
    try:
        val = float(term_spread)
    except (TypeError, ValueError):
        return np.nan
    return max(0.35, 0.45 + 0.18 * val)


def enrich_spread_columns(frame: pd.DataFrame) -> pd.DataFrame:
    """Add velocity / momentum / spread columns."""
    if frame.empty:
        return frame
    out = frame.copy()

    if "india_vix" in out.columns:
        out["india_vix_velocity_3d"] = compute_velocity_3d(out["india_vix"])

    if "usd_inr" in out.columns:
        out["usd_inr_momentum_5d"] = compute_momentum_5d(out["usd_inr"])

    if "us_10y" in out.columns:
        out["us_10y_velocity_3d"] = compute_velocity_3d(out["us_10y"])

    if "fii_net_5d" in out.columns:
        out["fii_net_5d_momentum"] = compute_level_momentum(out["fii_net_5d"], days=5)

    if "india_10y" in out.columns and "india_91d_tbill" in out.columns:
        out["india_term_spread"] = pd.to_numeric(out["india_10y"], errors="coerce") - pd.to_numeric(
            out["india_91d_tbill"], errors="coerce"
        )

    import os

    credit_override = os.getenv("INDEX_INDIA_CREDIT_SPREAD", "").strip()
    if credit_override:
        out["india_credit_spread"] = float(credit_override)
    else:
        out["india_credit_spread"] = np.nan

    return out


def spread_factor_rows_from_dict(factors: dict) -> list[dict]:
    """Velocity/momentum from two-point history unavailable — compute level spreads only."""
    rows: list[dict] = []
    if factors.get("india_10y") is not None and factors.get("india_91d_tbill") is not None:
        try:
            spread = float(factors["india_10y"]) - float(factors["india_91d_tbill"])
            rows.append({"factor": "india_term_spread", "value": round(spread, 4), "source": "spread_features"})
        except (TypeError, ValueError):
            pass
    if factors.get("india_credit_spread") is not None:
        try:
            rows.append(
                {
                    "factor": "india_credit_spread",
                    "value": float(factors["india_credit_spread"]),
                    "source": "spread_features",
                }
            )
        except (TypeError, ValueError):
            pass
    return rows


def phase_i_spread_series_for_history(frame: pd.DataFrame) -> dict[str, pd.Series]:
    if frame.empty or "date" not in frame.columns:
        return {}
    enriched = enrich_spread_columns(frame)
    idx = enriched["date"].astype(str)
    result: dict[str, pd.Series] = {}
    for key in SPREAD_OUTPUT_KEYS:
        if key not in enriched.columns:
            continue
        series = pd.to_numeric(enriched[key], errors="coerce")
        result[key] = pd.Series(series.values, index=idx)
    return result
