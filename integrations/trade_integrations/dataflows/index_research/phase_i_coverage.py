"""Phase I factor coverage gates (pre-mortem mitigation M3)."""

from __future__ import annotations

from typing import Any

import pandas as pd

PHASE_I_FACTOR_KEYS: tuple[str, ...] = (
    "nifty_earnings_yield",
    "nifty_dividend_yield",
    "nifty_pb",
    "nifty_book_to_market",
    "nifty_pb_zscore_5y",
    "equity_risk_premium",
    "india_10y",
    "india_91d_tbill",
    "india_term_spread",
    "india_credit_spread",
    "india_vix_velocity_3d",
    "usd_inr_momentum_5d",
    "us_10y_velocity_3d",
    "fii_net_5d_momentum",
)

_MIN_COVERAGE_RATIO = 0.45
_MIN_HISTORY_ROWS = 180


def audit_phase_i_coverage(frame: pd.DataFrame) -> dict[str, Any]:
    """Report coverage for Phase I columns on aligned history."""
    if frame.empty:
        return {"factors": [], "ridge_eligible": []}
    rows = len(frame)
    factors: list[dict[str, Any]] = []
    eligible: list[str] = []
    for key in PHASE_I_FACTOR_KEYS:
        if key not in frame.columns:
            factors.append({"factor": key, "coverage_pct": 0.0, "present": False})
            continue
        non_null = int(pd.to_numeric(frame[key], errors="coerce").notna().sum())
        pct = round(100.0 * non_null / rows, 1) if rows else 0.0
        factors.append({"factor": key, "coverage_pct": pct, "present": True, "non_null": non_null})
        if rows >= _MIN_HISTORY_ROWS and non_null >= int(rows * _MIN_COVERAGE_RATIO):
            eligible.append(key)
    return {
        "factors": factors,
        "ridge_eligible": eligible,
        "row_count": rows,
        "min_rows": _MIN_HISTORY_ROWS,
        "min_coverage_pct": _MIN_COVERAGE_RATIO * 100,
    }


def phase_i_keys_for_ridge(frame: pd.DataFrame) -> tuple[str, ...]:
    """Return Phase I keys that pass coverage gate for optional Ridge inclusion."""
    audit = audit_phase_i_coverage(frame)
    return tuple(audit.get("ridge_eligible") or [])
