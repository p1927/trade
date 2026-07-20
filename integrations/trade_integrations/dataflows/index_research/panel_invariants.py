"""Machine-checkable invariants for materialized prediction panels — fail closed on save."""

from __future__ import annotations

import os
from typing import Any

import numpy as np
import pandas as pd

from trade_integrations.dataflows.index_research.prediction_data_requirements import (
    REQUIRED_COLD_DATASETS,
)

_MIN_STD = 1e-12
_MIN_PINNED_COVERAGE = 0.45
_DEFAULT_WINDOW_DAYS = 500
_COLD_PARITY_TOLERANCE = 0.05
# Policy rates can be flat over multi-month windows without indicating data corruption.
_POLICY_FLAT_PINNED_OK: frozenset[str] = frozenset({"repo_rate", "india_91d_tbill"})

# Daily macro columns from macro_daily — must not be collapsed by annual joins.
DAILY_PROTECTED_FROM_ANNUAL: frozenset[str] = frozenset(
    {"sp500", "oil_brent", "oil_wti", "usd_inr", "gold", "us_10y"}
)
# Belt-and-suspenders: macro_daily daily FX/macro must never come from india_macro_annual.
ANNUAL_JOIN_BLOCKLIST: frozenset[str] = frozenset({"usd_inr"})

COLUMN_GRANULARITY: dict[str, str] = {
    "usd_inr": "daily",
    "oil_brent": "daily",
    "oil_wti": "daily",
    "gold": "daily",
    "sp500": "daily",
    "us_10y": "daily",
    "india_vix": "daily",
    "fii_net_5d": "daily",
    "dii_net_5d": "daily",
    "nifty_pcr": "daily",
    "repo_rate": "daily",
    "nifty_pe": "daily",
    "india_10y": "weekly",
    "india_91d_tbill": "weekly",
    "gdp_growth_pct": "annual",
    "inflation_pct": "annual",
    "sensex_return_pct": "annual",
}

PARENT_DERIVED_PAIRS: tuple[tuple[str, str], ...] = (
    ("usd_inr", "usd_inr_momentum_5d"),
    ("india_vix", "india_vix_velocity_3d"),
    ("us_10y", "us_10y_velocity_3d"),
)

_COLD_DATASET_BY_FACTOR: dict[str, str] = {}
for _dataset, _meta in REQUIRED_COLD_DATASETS.items():
    _factors = _meta.get("factors") or ()
    if isinstance(_factors, tuple):
        for _factor in _factors:
            if isinstance(_factor, str) and _factor not in _COLD_DATASET_BY_FACTOR:
                _COLD_DATASET_BY_FACTOR[_factor] = _dataset


class PanelInvariantError(ValueError):
    """Raised when a panel fails integrity checks before save."""


def pinned_factors_for_audit() -> frozenset[str]:
    from trade_integrations.dataflows.index_research.prediction_data_requirements import (
        pinned_factors,
    )

    return pinned_factors()


def _audit_window(frame: pd.DataFrame, *, window_days: int) -> pd.DataFrame:
    if frame.empty or "date" not in frame.columns:
        return frame
    out = frame.sort_values("date").reset_index(drop=True)
    if window_days > 0 and len(out) > window_days:
        return out.tail(window_days).reset_index(drop=True)
    return out


def _series_stats(series: pd.Series) -> dict[str, float | int]:
    numeric = pd.to_numeric(series, errors="coerce")
    non_null = int(numeric.notna().sum())
    rows = len(series)
    std = float(numeric.std(ddof=0)) if non_null > 1 else 0.0
    if not np.isfinite(std):
        std = 0.0
    return {
        "non_null": non_null,
        "rows": rows,
        "coverage": (non_null / rows) if rows else 0.0,
        "std": std,
        "nunique": int(numeric.nunique(dropna=True)),
    }


def check_pinned_factor_gates(
    frame: pd.DataFrame,
    *,
    window_days: int = _DEFAULT_WINDOW_DAYS,
) -> list[str]:
    """Return violation messages for pinned factors on the audit window."""
    violations: list[str] = []
    window = _audit_window(frame, window_days=window_days)
    if window.empty:
        return ["panel_empty"]

    for factor in sorted(pinned_factors_for_audit()):
        if factor not in window.columns:
            violations.append(f"pinned_missing:{factor}")
            continue
        stats = _series_stats(window[factor])
        if stats["coverage"] < _MIN_PINNED_COVERAGE:
            violations.append(
                f"pinned_sparse:{factor}:coverage={stats['coverage']:.3f}<{_MIN_PINNED_COVERAGE}"
            )
        if stats["non_null"] > 1 and stats["std"] <= _MIN_STD and factor not in _POLICY_FLAT_PINNED_OK:
            violations.append(f"pinned_flat:{factor}:std={stats['std']}")

    return violations


def check_parent_derived_pairs(frame: pd.DataFrame, *, window_days: int = _DEFAULT_WINDOW_DAYS) -> list[str]:
    violations: list[str] = []
    window = _audit_window(frame, window_days=window_days)
    for parent, derived in PARENT_DERIVED_PAIRS:
        if parent not in window.columns or derived not in window.columns:
            continue
        parent_std = _series_stats(window[parent])["std"]
        derived_stats = _series_stats(window[derived])
        derived_std = derived_stats["std"]
        if parent_std > _MIN_STD and derived_std <= _MIN_STD:
            derived_vals = pd.to_numeric(window[derived], errors="coerce").fillna(0.0)
            if float(derived_vals.abs().max()) <= _MIN_STD:
                violations.append(
                    f"derived_flat:{derived}:parent={parent}:parent_std={parent_std:.6f}"
                )
    return violations


def check_daily_protected_vs_cold(
    frame: pd.DataFrame,
    *,
    window_days: int = _DEFAULT_WINDOW_DAYS,
) -> list[str]:
    """Panel daily columns must not lose variance vs cold-tier parent on the audit window."""
    from trade_integrations.dataflows.index_research.history_store import load_history_dataset

    violations: list[str] = []
    window = _audit_window(frame, window_days=window_days)
    if window.empty or "date" not in window.columns:
        return violations

    dates = window["date"].astype(str).str[:10]
    start, end = str(dates.iloc[0]), str(dates.iloc[-1])

    for col in sorted(DAILY_PROTECTED_FROM_ANNUAL):
        if col not in window.columns:
            continue
        panel_std = _series_stats(window[col])["std"]
        dataset = _COLD_DATASET_BY_FACTOR.get(col, "macro_daily")
        cold = load_history_dataset(dataset)
        if cold.empty or col not in cold.columns:
            continue
        cold_slice = cold.copy()
        cold_slice["date"] = cold_slice["date"].astype(str).str[:10]
        cold_slice = cold_slice[(cold_slice["date"] >= start) & (cold_slice["date"] <= end)]
        cold_std = _series_stats(cold_slice[col])["std"]
        if cold_std > _MIN_STD and panel_std <= _MIN_STD:
            violations.append(
                f"daily_collapsed:{col}:panel_std={panel_std:.6f}:cold_std={cold_std:.6f}"
            )
        panel_cov = _series_stats(window[col])["coverage"]
        cold_cov = _series_stats(cold_slice[col])["coverage"]
        if cold_cov - panel_cov > _COLD_PARITY_TOLERANCE:
            violations.append(
                f"coverage_regression:{col}:panel={panel_cov:.3f}:cold={cold_cov:.3f}"
            )
    return violations


def check_panel_regression_vs_existing(
    candidate: pd.DataFrame,
    existing: pd.DataFrame,
    *,
    window_days: int = _DEFAULT_WINDOW_DAYS,
) -> list[str]:
    """Reject saves that collapse pinned factor variance vs the current production panel."""
    if existing.empty:
        return []
    violations: list[str] = []
    cand_w = _audit_window(candidate, window_days=window_days)
    exist_w = _audit_window(existing, window_days=window_days)
    for factor in sorted(pinned_factors_for_audit()):
        if factor not in cand_w.columns or factor not in exist_w.columns:
            continue
        old_std = _series_stats(exist_w[factor])["std"]
        new_std = _series_stats(cand_w[factor])["std"]
        if old_std > _MIN_STD and new_std <= _MIN_STD:
            violations.append(f"regression_flat:{factor}:was_std={old_std:.6f}:now_std={new_std:.6f}")
        old_cov = _series_stats(exist_w[factor])["coverage"]
        new_cov = _series_stats(cand_w[factor])["coverage"]
        if old_cov - new_cov > _COLD_PARITY_TOLERANCE:
            violations.append(
                f"regression_coverage:{factor}:was={old_cov:.3f}:now={new_cov:.3f}"
            )
    return violations


def audit_panel_invariants(
    frame: pd.DataFrame,
    *,
    window_days: int = _DEFAULT_WINDOW_DAYS,
    existing_panel: pd.DataFrame | None = None,
    skip_regression: bool = False,
) -> dict[str, Any]:
    """Run all invariant checks; return structured report."""
    violations: list[str] = []
    violations.extend(check_pinned_factor_gates(frame, window_days=window_days))
    violations.extend(check_parent_derived_pairs(frame, window_days=window_days))
    violations.extend(check_daily_protected_vs_cold(frame, window_days=window_days))
    if existing_panel is not None and not skip_regression:
        violations.extend(
            check_panel_regression_vs_existing(
                frame, existing_panel, window_days=window_days
            )
        )

    window = _audit_window(frame, window_days=window_days)
    factor_stats: dict[str, dict[str, float | int]] = {}
    for factor in sorted(pinned_factors_for_audit()):
        if factor in window.columns:
            factor_stats[factor] = _series_stats(window[factor])

    return {
        "ok": not violations,
        "violations": violations,
        "window_days": window_days,
        "window_rows": len(window),
        "pinned_factor_stats": factor_stats,
    }


def assert_panel_invariants(
    frame: pd.DataFrame,
    *,
    window_days: int = _DEFAULT_WINDOW_DAYS,
    existing_panel: pd.DataFrame | None = None,
    skip_regression: bool = False,
    force: bool = False,
) -> dict[str, Any]:
    """Raise PanelInvariantError when invariants fail (unless force=True)."""
    if force or os.getenv("INDEX_PANEL_SAVE_FORCE", "").strip().lower() in {"1", "true", "yes"}:
        report = audit_panel_invariants(
            frame,
            window_days=window_days,
            existing_panel=existing_panel,
            skip_regression=True,
        )
        report["forced"] = True
        return report

    report = audit_panel_invariants(
        frame,
        window_days=window_days,
        existing_panel=existing_panel,
        skip_regression=skip_regression,
    )
    if not report["ok"]:
        msg = "; ".join(report["violations"][:8])
        if len(report["violations"]) > 8:
            msg += f"; ... (+{len(report['violations']) - 8} more)"
        raise PanelInvariantError(msg)
    return report


def factor_stats_snapshot(frame: pd.DataFrame, *, window_days: int = _DEFAULT_WINDOW_DAYS) -> dict[str, Any]:
    """Stats blob for panel_manifest.json sidecar."""
    window = _audit_window(frame, window_days=window_days)
    stats = {}
    for factor in sorted(pinned_factors_for_audit()):
        if factor in window.columns:
            stats[factor] = _series_stats(window[factor])
    return {
        "window_days": window_days,
        "window_rows": len(window),
        "factors": stats,
    }
