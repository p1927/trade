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

PHASE_I_ABLATION_BLOCKS: dict[str, tuple[str, ...]] = {
    "phase_i_valuation": (
        "nifty_earnings_yield",
        "nifty_dividend_yield",
        "nifty_pb",
        "nifty_book_to_market",
        "nifty_pb_zscore_5y",
        "equity_risk_premium",
    ),
    "phase_i_liquidity": (
        "india_10y",
        "india_91d_tbill",
        "india_term_spread",
        "india_credit_spread",
    ),
    "phase_i_flow_momentum": (
        "india_vix_velocity_3d",
        "usd_inr_momentum_5d",
        "us_10y_velocity_3d",
        "fii_net_5d_momentum",
    ),
}

PHASE_I_PROXY_FACTORS: frozenset[str] = frozenset(
    {
        "nifty_dividend_yield",
        "nifty_pb",
        "nifty_book_to_market",
        "nifty_pb_zscore_5y",
        "india_10y",
        "india_91d_tbill",
        "india_credit_spread",
        "equity_risk_premium",
    }
)

_MIN_COVERAGE_RATIO = 0.45
_MIN_HISTORY_ROWS = 180
_ABLATION_ACCEPT_PP = 3.0


def audit_phase_i_coverage(frame: pd.DataFrame) -> dict[str, Any]:
    """Report coverage for Phase I columns on aligned history."""
    if frame.empty:
        return {"factors": [], "ridge_eligible": [], "proxy_factors": list(PHASE_I_PROXY_FACTORS)}
    rows = len(frame)
    factors: list[dict[str, Any]] = []
    eligible: list[str] = []
    for key in PHASE_I_FACTOR_KEYS:
        if key not in frame.columns:
            factors.append(
                {
                    "factor": key,
                    "coverage_pct": 0.0,
                    "present": False,
                    "data_quality": "proxy" if key in PHASE_I_PROXY_FACTORS else "missing",
                }
            )
            continue
        non_null = int(pd.to_numeric(frame[key], errors="coerce").notna().sum())
        pct = round(100.0 * non_null / rows, 1) if rows else 0.0
        quality = "proxy" if key in PHASE_I_PROXY_FACTORS else "observed"
        factors.append(
            {
                "factor": key,
                "coverage_pct": pct,
                "present": True,
                "non_null": non_null,
                "data_quality": quality,
            }
        )
        if rows >= _MIN_HISTORY_ROWS and non_null >= int(rows * _MIN_COVERAGE_RATIO):
            eligible.append(key)
    return {
        "factors": factors,
        "ridge_eligible": eligible,
        "proxy_factors": sorted(PHASE_I_PROXY_FACTORS),
        "row_count": rows,
        "min_rows": _MIN_HISTORY_ROWS,
        "min_coverage_pct": _MIN_COVERAGE_RATIO * 100,
        "ablation": summarize_phase_i_ablation(),
    }


def summarize_phase_i_ablation(*, ticker: str = "NIFTY") -> dict[str, Any]:
    """Summarize Phase I block ablation from saved equation diagnostics when present."""
    from trade_integrations.dataflows.index_research.equation_diagnostics import load_diagnostics_report

    report = load_diagnostics_report(ticker) or {}
    block_rows = {row.get("block"): row for row in (report.get("block_ablation") or []) if row.get("block")}
    groups: list[dict[str, Any]] = []
    for block_name, keys in PHASE_I_ABLATION_BLOCKS.items():
        row = block_rows.get(block_name) or {}
        delta_pp = row.get("delta_pp")
        groups.append(
            {
                "block": block_name,
                "factors": list(keys),
                "delta_pp": delta_pp,
                "promotion_ready": delta_pp is not None and float(delta_pp) >= _ABLATION_ACCEPT_PP,
                "diagnostics_available": bool(row),
            }
        )
    available = sum(1 for g in groups if g["diagnostics_available"])
    return {
        "accept_gate_pp": _ABLATION_ACCEPT_PP,
        "diagnostics_as_of": report.get("as_of"),
        "baseline_direction_hit_rate": report.get("baseline_direction_hit_rate"),
        "groups": groups,
        "diagnostics_available": available > 0,
        "run_command": "python scripts/run_equation_diagnostics.py --days 365 --ticker "
        + ticker.strip().upper(),
    }


def phase_i_keys_for_ridge(frame: pd.DataFrame) -> tuple[str, ...]:
    """Return Phase I keys that pass coverage gate for optional Ridge inclusion."""
    audit = audit_phase_i_coverage(frame)
    return tuple(audit.get("ridge_eligible") or [])
