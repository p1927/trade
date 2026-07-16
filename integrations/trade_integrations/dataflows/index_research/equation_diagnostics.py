"""Equation diagnostics: block ablation, sign conflicts, coefficient stability."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from trade_integrations.context.hub import get_hub_dir
from trade_integrations.dataflows.index_research.backtest_runner import (
    _forward_return_pct,
    load_backtest_report,
)
from trade_integrations.dataflows.index_research.factor_matrix import MACRO_FACTOR_KEYS, build_factor_matrix
from trade_integrations.dataflows.index_research.horizon import resolve_horizon
from trade_integrations.dataflows.index_research.predictor import (
    _predict_macro_delta,
    cap_macro_delta,
    train_macro_ridge,
)
from trade_integrations.dataflows.index_research.sources.history_loader import load_aligned_factor_history

_MIN_TRAIN_ROWS = 45
_DEFAULT_EVAL_STEP = 5

FACTOR_BLOCKS: dict[str, list[str]] = {
    "momentum": [
        "nifty_return_7d",
        "nifty_return_14d",
        "nifty_rsi_14",
        "nifty_ma20_distance_pct",
        "constituent_momentum_7d",
    ],
    "flows": ["fii_net_5d", "dii_net_5d", "fii_fut_long_short_ratio", "nifty_pcr"],
    "global": ["oil_brent", "oil_wti", "usd_inr", "gold", "sp500", "us_10y"],
    "vol": ["india_vix", "nifty_realized_vol_20d"],
    "calendar": ["days_to_monthly_expiry", "is_budget_week", "is_results_season"],
    "delta": [
        "fii_net_5d_change_5d",
        "dii_net_5d_change_5d",
        "oil_brent_change_7d",
        "india_vix_change_5d",
    ],
}

LITERATURE_SIGNS: dict[str, str] = {
    "fii_net_5d": "positive",
    "dii_net_5d": "positive",
    "oil_brent": "negative",
    "india_vix": "negative",
    "nifty_return_14d": "positive",
    "constituent_momentum_7d": "positive",
    "nifty_ma20_distance_pct": "positive",
}

LOGIC_CONFLICTS: list[dict[str, str]] = [
    {
        "conflict": "momentum_block_vs_literature",
        "logic": "Mean-reversion terms fight momentum literature — mutually exclusive; need regime gate.",
    },
    {
        "conflict": "fii_contrarian_vs_acceleration",
        "logic": "Contrarian FII level works range-bound; fails when selling accelerates — need delta feature.",
    },
    {
        "conflict": "dii_zero_coef_low_coverage",
        "logic": "DII positive corr but ~50% coverage — fix data before interpreting coefficient.",
    },
    {
        "conflict": "static_levels_vs_14d_target",
        "logic": "Factor levels at T0 under-specify path — need change features over horizon.",
    },
]


def _diagnostics_path(ticker: str = "NIFTY") -> Path:
    return get_hub_dir() / ticker.strip().upper() / "index_research" / "equation_diagnostics_latest.json"


def save_diagnostics_report(report: dict[str, Any], *, ticker: str = "NIFTY") -> Path:
    path = _diagnostics_path(ticker)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    return path


def load_diagnostics_report(ticker: str = "NIFTY") -> dict[str, Any] | None:
    path = _diagnostics_path(ticker)
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _feature_columns(frame: pd.DataFrame) -> list[str]:
    exclude = {"date", "close", "target", "realized_1d_pct"}
    return [
        c
        for c in frame.columns
        if c not in exclude and pd.api.types.is_numeric_dtype(frame[c])
    ]


def _row_factor_dict(row: pd.Series, feature_cols: list[str]) -> dict[str, float]:
    out: dict[str, float] = {}
    for col in feature_cols:
        val = row.get(col)
        if pd.notna(val):
            out[col] = float(val)
    return out


def _walk_forward_hit_rate(
    frame: pd.DataFrame,
    *,
    horizon_days: int,
    min_train_rows: int,
    eval_step: int,
    exclude_factors: set[str] | None = None,
) -> float | None:
    """Direction hit rate with optional factor exclusion (block ablation)."""
    horizon = resolve_horizon(horizon_days)
    work = frame.copy()
    if exclude_factors:
        for col in exclude_factors:
            if col in work.columns:
                work[col] = np.nan

    work["target"] = _forward_return_pct(work["close"].astype(float), horizon.days)
    feature_cols = _feature_columns(work)
    directions_hit = 0
    directions_total = 0
    max_i = len(work) - horizon.days - 1
    indices = list(range(min_train_rows, max_i + 1, max(1, eval_step)))

    for i in indices:
        train = work.iloc[:i].copy()
        row = work.iloc[i]
        actual = row["target"]
        if pd.isna(actual):
            continue
        try:
            artifact = train_macro_ridge(train, horizon)
        except (ValueError, ImportError):
            continue
        if not artifact.feature_names:
            continue
        factors = _row_factor_dict(row, feature_cols)
        raw = _predict_macro_delta(factors, horizon, artifact)
        predicted = cap_macro_delta(raw)
        pred_dir = predicted > 0
        actual_dir = float(actual) > 0
        if pred_dir == actual_dir:
            directions_hit += 1
        directions_total += 1

    return directions_hit / directions_total if directions_total else None


def _factor_correlations(frame: pd.DataFrame, horizon_days: int) -> list[dict[str, Any]]:
    work = frame.copy().sort_values("date")
    work["target"] = _forward_return_pct(work["close"].astype(float), horizon_days)
    usable = work.dropna(subset=["target"])
    if usable.empty:
        return []
    y = usable["target"]
    rows: list[dict[str, Any]] = []
    for col in _feature_columns(work):
        series = pd.to_numeric(usable[col], errors="coerce")
        if series.notna().sum() < 10 or series.std(ddof=0) == 0:
            continue
        corr = series.corr(y)
        if corr is None or np.isnan(corr):
            continue
        rows.append({"factor": col, "corr_forward_return": round(float(corr), 4)})
    rows.sort(key=lambda r: abs(r["corr_forward_return"]), reverse=True)
    return rows


def _redundant_pairs(frame: pd.DataFrame, *, threshold: float = 0.7) -> list[dict[str, Any]]:
    cols = _feature_columns(frame)
    if len(cols) < 2:
        return []
    numeric = frame[cols].apply(pd.to_numeric, errors="coerce")
    corr = numeric.corr()
    pairs: list[dict[str, Any]] = []
    for i, a in enumerate(cols):
        for b in cols[i + 1 :]:
            val = corr.loc[a, b] if a in corr.index and b in corr.columns else None
            if val is None or np.isnan(val) or abs(val) < threshold:
                continue
            pairs.append({"factor_a": a, "factor_b": b, "correlation": round(float(val), 4)})
    pairs.sort(key=lambda r: abs(r["correlation"]), reverse=True)
    return pairs[:20]


def _sign_conflicts(artifact_coefs: dict[str, float], correlations: list[dict[str, Any]]) -> list[dict[str, Any]]:
    corr_map = {r["factor"]: r["corr_forward_return"] for r in correlations}
    conflicts: list[dict[str, Any]] = []
    for factor, expected in LITERATURE_SIGNS.items():
        # Find linear term coef (not interaction)
        coef = artifact_coefs.get(factor)
        if coef is None:
            continue
        corr = corr_map.get(factor)
        if corr is None:
            continue
        expected_sign = 1 if expected == "positive" else -1
        coef_sign = 1 if coef > 0 else -1 if coef < 0 else 0
        corr_sign = 1 if corr > 0 else -1 if corr < 0 else 0
        if coef_sign and coef_sign != expected_sign:
            conflicts.append(
                {
                    "factor": factor,
                    "coefficient": round(coef, 6),
                    "corr_forward_return": corr,
                    "literature_sign": expected,
                    "conflict": "coef_vs_literature",
                }
            )
        if coef_sign and corr_sign and coef_sign != corr_sign:
            conflicts.append(
                {
                    "factor": factor,
                    "coefficient": round(coef, 6),
                    "corr_forward_return": corr,
                    "literature_sign": expected,
                    "conflict": "coef_vs_forward_corr",
                }
            )
    return conflicts


def _coefficient_stability(
    frame: pd.DataFrame,
    *,
    horizon_days: int,
    min_train_rows: int,
    eval_step: int,
) -> list[dict[str, Any]]:
    horizon = resolve_horizon(horizon_days)
    work = frame.copy()
    work["target"] = _forward_return_pct(work["close"].astype(float), horizon.days)
    max_i = len(work) - horizon.days - 1
    indices = list(range(min_train_rows, max_i + 1, max(1, eval_step)))

    term_signs: dict[str, list[int]] = {}
    for i in indices:
        train = work.iloc[:i].copy()
        try:
            artifact = train_macro_ridge(train, horizon)
        except (ValueError, ImportError):
            continue
        for term, coef in (artifact.coefficients or {}).items():
            if abs(coef) < 1e-9:
                continue
            sign = 1 if coef > 0 else -1
            term_signs.setdefault(term, []).append(sign)

    unstable: list[dict[str, Any]] = []
    for term, signs in term_signs.items():
        if len(signs) < 2:
            continue
        flips = sum(1 for j in range(1, len(signs)) if signs[j] != signs[j - 1])
        if flips >= max(2, len(signs) // 3):
            unstable.append({"term": term, "refits": len(signs), "sign_flips": flips})
    unstable.sort(key=lambda r: r["sign_flips"], reverse=True)
    return unstable[:15]


def _regime_correlation_matrix(frame: pd.DataFrame, horizon_days: int) -> dict[str, list[dict[str, Any]]]:
    work = frame.copy().sort_values("date")
    work["target"] = _forward_return_pct(work["close"].astype(float), horizon_days)
    if "trend_20d_pct" not in work.columns and "nifty_return_14d" in work.columns:
        work["trend_20d_pct"] = work["nifty_return_14d"]

    regimes: dict[str, pd.Series] = {}
    if "india_vix" in work.columns:
        vix = pd.to_numeric(work["india_vix"], errors="coerce")
        regimes["high_fear"] = vix > 18
        regimes["low_fear"] = vix <= 18
    if "trend_20d_pct" in work.columns:
        trend = pd.to_numeric(work["trend_20d_pct"], errors="coerce")
        regimes["trend_down"] = trend < -3.0
        regimes["trend_up"] = trend > 3.0

    out: dict[str, list[dict[str, Any]]] = {}
    for name, mask in regimes.items():
        subset = work[mask.fillna(False)]
        out[name] = _factor_correlations(subset, horizon_days)[:8]
    return out


def run_equation_diagnostics(
    *,
    days: int = 365,
    horizon_days: int = 14,
    min_train_rows: int = _MIN_TRAIN_ROWS,
    eval_step: int = _DEFAULT_EVAL_STEP,
    ticker: str = "NIFTY",
) -> dict[str, Any]:
    aligned = load_aligned_factor_history(days=days)
    if aligned.empty:
        return {"status": "error", "message": "no aligned history"}

    frame = aligned.sort_values("date").reset_index(drop=True)
    horizon = resolve_horizon(horizon_days)
    baseline_hit = _walk_forward_hit_rate(
        frame,
        horizon_days=horizon_days,
        min_train_rows=min_train_rows,
        eval_step=eval_step,
    )

    block_ablation: list[dict[str, Any]] = []
    for block_name, factors in FACTOR_BLOCKS.items():
        exclude = {f for f in factors if f in frame.columns}
        if not exclude:
            continue
        hit = _walk_forward_hit_rate(
            frame,
            horizon_days=horizon_days,
            min_train_rows=min_train_rows,
            eval_step=eval_step,
            exclude_factors=exclude,
        )
        block_ablation.append(
            {
                "block": block_name,
                "factors": list(exclude),
                "direction_hit_rate_without_block": round(hit, 4) if hit is not None else None,
                "baseline_hit_rate": round(baseline_hit, 4) if baseline_hit is not None else None,
                "delta_pp": round((hit - baseline_hit) * 100, 2)
                if hit is not None and baseline_hit is not None
                else None,
            }
        )

    correlations = _factor_correlations(frame, horizon_days)
    try:
        full_artifact = train_macro_ridge(frame, horizon)
        sign_conflicts = _sign_conflicts(full_artifact.coefficients, correlations)
    except (ValueError, ImportError):
        sign_conflicts = []

    backtest = load_backtest_report(ticker)
    stored_corr = (backtest or {}).get("factor_correlations") or correlations

    return {
        "status": "ok",
        "as_of": datetime.now(timezone.utc).isoformat(),
        "ticker": ticker,
        "horizon_days": horizon_days,
        "baseline_direction_hit_rate": round(baseline_hit, 4) if baseline_hit is not None else None,
        "factor_correlations": stored_corr if stored_corr else correlations,
        "block_ablation": block_ablation,
        "sign_conflicts": sign_conflicts,
        "redundant_pairs": _redundant_pairs(frame),
        "unstable_terms": _coefficient_stability(
            frame,
            horizon_days=horizon_days,
            min_train_rows=min_train_rows,
            eval_step=eval_step,
        ),
        "regime_correlation_matrix": _regime_correlation_matrix(frame, horizon_days),
        "logic_conflict_register": LOGIC_CONFLICTS,
    }


def run_and_save_diagnostics(**kwargs: Any) -> dict[str, Any]:
    report = run_equation_diagnostics(**kwargs)
    if report.get("status") == "ok":
        save_diagnostics_report(report, ticker=str(kwargs.get("ticker") or "NIFTY"))
    return report
