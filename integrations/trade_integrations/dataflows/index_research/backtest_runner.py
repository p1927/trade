"""Walk-forward backtest and factor audit for index prediction using historical data."""

from __future__ import annotations

import json
import logging
import os
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from trade_integrations.context.hub import get_hub_dir
from trade_integrations.dataflows.index_research.attribution import (
    attribute_constituents,
    rollup_attribution,
)
from trade_integrations.dataflows.index_research.models import ConstituentSignal
from trade_integrations.dataflows.index_research.calendar_features import (
    calendar_factor_dict,
    days_to_monthly_expiry,
    is_budget_week,
    is_results_season,
)
from trade_integrations.dataflows.index_research.factor_matrix import (
    MACRO_FACTOR_KEYS,
    build_factor_matrix,
    redundancy_audit,
)
from trade_integrations.dataflows.index_research.horizon import resolve_horizon
from trade_integrations.dataflows.index_research.predictor import (
    shrink_macro_delta,
    train_macro_ridge,
)
from trade_integrations.dataflows.index_research.drawdown_attribution import enrich_drawdowns
from trade_integrations.dataflows.index_research.sources.history_loader import (
    load_aligned_factor_history,
)

logger = logging.getLogger(__name__)

_FACTOR_LABELS: dict[str, str] = {
    "oil_brent": "Brent crude",
    "oil_wti": "WTI crude",
    "usd_inr": "USD/INR",
    "gold": "Gold",
    "sp500": "S&P 500",
    "us_10y": "US 10Y yield",
    "india_vix": "India VIX",
    "repo_rate": "Repo rate",
    "nifty_return_7d": "Nifty 7d return",
    "nifty_return_14d": "Nifty 14d return",
    "nifty_rsi_14": "Nifty RSI-14",
    "nifty_realized_vol_20d": "Nifty 20d vol",
    "nifty_ma20_distance_pct": "Distance from MA20",
    "days_to_monthly_expiry": "Days to monthly expiry",
    "is_budget_week": "Budget week",
    "is_results_season": "Results season",
    "fii_net_5d": "FII net (5d)",
    "dii_net_5d": "DII net (5d)",
    "fii_fut_long_short_ratio": "FII index fut long/short",
    "nifty_pcr": "Nifty PCR",
    "constituent_momentum_7d": "Constituent momentum (7d)",
}

_MIN_TRAIN_ROWS = 45
_DEFAULT_EVAL_STEP = 5
_DEFAULT_BACKTEST_DAYS = 500
_MIN_HYBRID_CONSTITUENTS = 8
_HYBRID_COVERAGE_AUTO_THRESHOLD = 45.0


def _company_history_path(symbol: str, day: str) -> Path:
    return get_hub_dir() / symbol.strip().upper() / "company_research" / "history" / f"{day[:10]}.json"


def _bottom_up_from_archives(day: str, *, horizon_days: int) -> float | None:
    """Replay bottom-up attribution when archived company_research/history exists."""
    from trade_integrations.dataflows.index_research.constituent_backtest import (
        bottom_up_return_from_archives,
    )

    return bottom_up_return_from_archives(day, horizon_days=horizon_days)


def _backtest_report_path(ticker: str = "NIFTY") -> Path:
    return get_hub_dir() / ticker.strip().upper() / "index_research" / "backtest_latest.json"


def save_backtest_report(report: dict[str, Any], *, ticker: str = "NIFTY") -> Path:
    path = _backtest_report_path(ticker)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    return path


def load_backtest_report(ticker: str = "NIFTY") -> dict[str, Any] | None:
    path = _backtest_report_path(ticker)
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _forward_return_pct(close: pd.Series, horizon_days: int) -> pd.Series:
    future = close.shift(-horizon_days)
    return (future - close) / close * 100.0


def _row_factor_dict(row: pd.Series, columns: list[str]) -> dict[str, float]:
    out: dict[str, float] = {}
    for col in columns:
        if col not in row.index:
            continue
        val = row[col]
        if pd.isna(val):
            continue
        try:
            out[col] = float(val)
        except (TypeError, ValueError):
            continue
    return out


def _calendar_events_for_date(as_of: date) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    if is_results_season(as_of) >= 1.0:
        events.append(
            {
                "type": "calendar",
                "event": "results_season",
                "description": "Indian earnings results season (peak months)",
            }
        )
    if is_budget_week(as_of) >= 1.0:
        events.append(
            {
                "type": "calendar",
                "event": "union_budget",
                "description": "Union Budget week",
            }
        )
    expiry_days = days_to_monthly_expiry(as_of)
    if expiry_days <= 3:
        events.append(
            {
                "type": "calendar",
                "event": "monthly_expiry",
                "description": f"Monthly F&O expiry in {expiry_days} day(s)",
            }
        )
    return events


def _factor_drivers(
    current: dict[str, float],
    previous: dict[str, float],
    *,
    limit: int = 5,
) -> list[dict[str, Any]]:
    drivers: list[dict[str, Any]] = []
    for key in set(current) | set(previous):
        cur = current.get(key)
        prev = previous.get(key)
        if cur is None or prev is None:
            continue
        if prev == 0:
            pct_chg = 0.0 if cur == 0 else 100.0
        else:
            pct_chg = (cur - prev) / abs(prev) * 100.0
        drivers.append(
            {
                "factor": key,
                "label": _FACTOR_LABELS.get(key, key),
                "prev": round(prev, 4),
                "current": round(cur, 4),
                "change_pct": round(pct_chg, 2),
            }
        )
    drivers.sort(key=lambda d: abs(d["change_pct"]), reverse=True)
    return drivers[:limit]


def audit_factor_coverage(aligned: pd.DataFrame) -> list[dict[str, Any]]:
    """Report non-null coverage per factor column in aligned history."""
    if aligned.empty:
        return []
    rows = len(aligned)
    exclude = {"date", "close", "open", "high", "low", "volume", "target"}
    cols = [c for c in aligned.columns if c not in exclude and pd.api.types.is_numeric_dtype(aligned[c])]
    audit: list[dict[str, Any]] = []
    for col in sorted(cols):
        non_null = int(aligned[col].notna().sum())
        static = aligned[col].nunique(dropna=True) <= 1
        audit.append(
            {
                "factor": col,
                "label": _FACTOR_LABELS.get(col, col),
                "rows_present": non_null,
                "rows_total": rows,
                "coverage_pct": round(100.0 * non_null / rows, 1) if rows else 0.0,
                "is_static": static,
                "in_macro_keys": col in MACRO_FACTOR_KEYS,
            }
        )
    return audit


def find_major_drawdowns(
    aligned: pd.DataFrame,
    *,
    threshold_1d_pct: float = -1.0,
    limit: int = 20,
) -> list[dict[str, Any]]:
    """Identify largest single-day Nifty drops and factor moves that day."""
    if aligned.empty or "close" not in aligned.columns:
        return []

    frame = aligned.sort_values("date").reset_index(drop=True)
    closes = frame["close"].astype(float)
    frame["realized_1d_pct"] = (closes - closes.shift(1)) / closes.shift(1) * 100.0

    exclude = {"date", "close", "target", "realized_1d_pct"}
    feature_cols = [
        c
        for c in frame.columns
        if c not in exclude and pd.api.types.is_numeric_dtype(frame[c])
    ]

    drops = frame[frame["realized_1d_pct"] <= threshold_1d_pct].copy()
    drops = drops.sort_values("realized_1d_pct").head(limit)

    rows: list[dict[str, Any]] = []
    factor_by_date: dict[str, dict[str, float]] = {}
    for _, row in frame.iterrows():
        day = str(row["date"])[:10]
        factor_by_date[day] = _row_factor_dict(row, feature_cols)

    for _, row in drops.iterrows():
        day = str(row["date"])[:10]
        idx = int(row.name)
        prev_day = str(frame.iloc[idx - 1]["date"])[:10] if idx > 0 else day
        factors_today = factor_by_date.get(day, {})
        factors_prev = factor_by_date.get(prev_day, {})
        try:
            as_of = date.fromisoformat(day)
        except ValueError:
            as_of = date.today()

        rows.append(
            {
                "date": day,
                "spot": round(float(row["close"]), 2),
                "realized_1d_pct": round(float(row["realized_1d_pct"]), 3),
                "factor_drivers": _factor_drivers(factors_today, factors_prev, limit=8),
                "calendar_events": _calendar_events_for_date(as_of),
            }
        )

    return rows


def audit_factor_correlations(
    aligned: pd.DataFrame,
    *,
    horizon_days: int = 14,
) -> list[dict[str, Any]]:
    """Rank factors by |correlation| with forward Nifty return."""
    if aligned.empty or "close" not in aligned.columns:
        return []
    frame = aligned.copy().sort_values("date")
    frame["target"] = _forward_return_pct(frame["close"].astype(float), horizon_days)
    usable = frame.dropna(subset=["target"])
    if usable.empty:
        return []
    exclude = {"date", "close", "target"}
    cols = [c for c in usable.columns if c not in exclude and pd.api.types.is_numeric_dtype(usable[c])]
    ranked: list[dict[str, Any]] = []
    y = usable["target"]
    for col in cols:
        series = pd.to_numeric(usable[col], errors="coerce")
        if series.notna().sum() < 10 or series.std(ddof=0) == 0:
            continue
        corr = series.corr(y)
        if corr is None or np.isnan(corr):
            continue
        ranked.append(
            {
                "factor": col,
                "label": _FACTOR_LABELS.get(col, col),
                "corr_forward_return": round(float(corr), 4),
            }
        )
    ranked.sort(key=lambda r: abs(r["corr_forward_return"]), reverse=True)
    return ranked[:15]


def _resolve_include_bottom_up(
    include_bottom_up: bool | str,
    trading_dates: list[str],
) -> tuple[bool, dict[str, Any]]:
    """Auto-enable hybrid replay when constituent archive coverage is sufficient."""
    from trade_integrations.dataflows.index_research.constituent_backtest import (
        bottom_up_archive_coverage,
    )

    coverage = bottom_up_archive_coverage(trading_dates)
    if include_bottom_up is True:
        return True, coverage
    if include_bottom_up is False:
        return False, coverage
    auto = float(coverage.get("coverage_pct") or 0.0) >= _HYBRID_COVERAGE_AUTO_THRESHOLD
    return auto, coverage


def run_walk_forward_backtest(
    *,
    days: int = _DEFAULT_BACKTEST_DAYS,
    horizon_days: int | None = 14,
    min_train_rows: int = _MIN_TRAIN_ROWS,
    eval_step: int = _DEFAULT_EVAL_STEP,
    include_bottom_up: bool | str = "auto",
    eval_protocol: str = "purged_expanding",
) -> dict[str, Any]:
    """Expanding-window macro backtest on aligned Nifty + factor history."""
    horizon = resolve_horizon(horizon_days)
    aligned = load_aligned_factor_history(days=days)
    if aligned.empty:
        return {"status": "error", "message": "No aligned factor history"}

    frame = aligned.sort_values("date").reset_index(drop=True)
    frame["target"] = _forward_return_pct(frame["close"].astype(float), horizon.days)
    closes = frame["close"].astype(float)
    frame["realized_1d_pct"] = (closes - closes.shift(1)) / closes.shift(1) * 100.0
    nifty_series = [
        {
            "date": str(row["date"])[:10],
            "close": round(float(row["close"]), 2),
            "realized_1d_pct": round(float(row["realized_1d_pct"]), 3)
            if pd.notna(row["realized_1d_pct"])
            else None,
        }
        for _, row in frame.iterrows()
    ]
    feature_cols = [
        c
        for c in frame.columns
        if c not in {"date", "close", "target"}
        and pd.api.types.is_numeric_dtype(frame[c])
    ]

    from trade_integrations.dataflows.index_research.walk_forward_utils import (
        bootstrap_direction_ci,
        purged_train_end_index,
        sign_magnitude_score,
    )

    trading_dates = frame["date"].astype(str).str[:10].tolist()
    hybrid_enabled, bottom_up_coverage = _resolve_include_bottom_up(include_bottom_up, trading_dates)

    eval_rows: list[dict[str, Any]] = []
    errors: list[float] = []
    directions_hit = 0
    directions_total = 0
    hybrid_hits = 0
    hybrid_total = 0

    max_i = len(frame) - horizon.days - 1
    indices = list(range(min_train_rows, max_i + 1, max(1, eval_step)))

    prev_factors: dict[str, float] = {}
    for i in indices:
        train_end = purged_train_end_index(
            i,
            horizon_days=horizon.days,
            eval_step=eval_step,
        )
        if train_end < min_train_rows:
            continue
        train = frame.iloc[:train_end].copy()
        row = frame.iloc[i]
        actual = row["target"]
        if pd.isna(actual):
            continue

        try:
            artifact = train_macro_ridge(train, horizon)
        except (ValueError, ImportError) as exc:
            logger.debug("backtest skip train at %s: %s", i, exc)
            continue

        if not artifact.feature_names:
            continue

        factors_today = _row_factor_dict(row, feature_cols)
        from trade_integrations.dataflows.index_research.regime_gates import (
            predict_macro_delta_gated,
            resolve_regime_label,
        )
        from trade_integrations.dataflows.index_research.scenarios import (
            build_index_scenarios,
            scenario_weighted_return_pct,
        )

        close = float(row["close"])
        day_str = str(row["date"])[:10]
        scenario_anchor = None
        try:
            scenarios = build_index_scenarios([], factors_today, spot=close, horizon_days=horizon.days)
            scenario_anchor = scenario_weighted_return_pct(scenarios, spot=close)
        except Exception:
            scenario_anchor = None
        from trade_integrations.dataflows.index_research.macro_forecast import compute_macro_only_return

        macro, _macro_prov = compute_macro_only_return(
            factors_today,
            horizon,
            artifact,
            scenario_anchor=scenario_anchor,
            as_of_day=day_str,
        )
        _overlay = {"return_pct": _macro_prov.get("event_overlay_pct")}
        predicted = macro  # macro-only backtest (no historical constituent research)
        regime_label = resolve_regime_label(factors_today)
        from trade_integrations.dataflows.index_research.flow_regime_buckets import (
            flow_regime_bucket,
        )

        flow_bucket = flow_regime_bucket(factors_today, regime_label)
        bottom_up = None
        hybrid_predicted = None
        if hybrid_enabled:
            bottom_up = _bottom_up_from_archives(day_str, horizon_days=horizon.days)
            if bottom_up is not None:
                hybrid_predicted = bottom_up + macro

        err = float(predicted) - float(actual)
        errors.append(abs(err))
        pred_dir = predicted > 0
        actual_dir = float(actual) > 0
        if pred_dir == actual_dir:
            directions_hit += 1
        directions_total += 1
        if hybrid_predicted is not None:
            if (hybrid_predicted > 0) == actual_dir:
                hybrid_hits += 1
            hybrid_total += 1

        close = float(row["close"])
        prev_close = float(frame.iloc[i - 1]["close"]) if i > 0 else close
        realized_1d_pct = (close - prev_close) / prev_close * 100.0 if prev_close else 0.0

        drivers = _factor_drivers(factors_today, prev_factors)
        prev_factors = dict(factors_today)

        eval_rows.append(
            {
                "date": day_str,
                "spot": round(close, 2),
                "realized_1d_pct": round(realized_1d_pct, 3),
                "predicted_return_pct": round(predicted, 3),
                "actual_forward_return_pct": round(float(actual), 3),
                "error_pct": round(err, 3),
                "direction_correct": pred_dir == actual_dir,
                "regime_label": regime_label,
                "flow_regime_bucket": flow_bucket,
                "macro_delta_pct": round(macro, 3),
                "macro_raw_pct": round(_macro_prov.get("raw_macro_delta_pct") or 0.0, 3),
                "event_overlay_pct": round(_overlay.get("return_pct") or 0.0, 3) if _overlay else None,
                "bottom_up_return_pct": round(bottom_up, 3) if bottom_up is not None else None,
                "hybrid_predicted_return_pct": round(hybrid_predicted, 3)
                if hybrid_predicted is not None
                else None,
                "ridge_alpha": getattr(artifact, "ridge_alpha", None),
                "poly_degree": getattr(artifact, "poly_degree", None),
                "sign_magnitude_score": round(sign_magnitude_score(predicted, float(actual)), 4),
                "factor_drivers": drivers,
                "calendar_events": _calendar_events_for_date(
                    date.fromisoformat(day_str) if len(day_str) == 10 else date.today()
                ),
                "implied_level": round(close * (1.0 + predicted / 100.0), 2),
            }
        )

    regime_buckets: dict[str, dict[str, int | float | None]] = {}
    for label in ("high_fear", "trend_down", "range_bound"):
        bucket = [r for r in eval_rows if r.get("regime_label") == label]
        total = len(bucket)
        hits = sum(1 for r in bucket if r.get("direction_correct"))
        regime_buckets[label] = {
            "eval_count": total,
            "direction_hits": hits,
            "direction_hit_rate": round(hits / total, 4) if total else None,
        }

    flow_regime_buckets: dict[str, dict[str, int | float | None]] = {}
    flow_labels = sorted({str(r.get("flow_regime_bucket") or "") for r in eval_rows if r.get("flow_regime_bucket")})
    for label in flow_labels:
        bucket = [r for r in eval_rows if r.get("flow_regime_bucket") == label]
        total = len(bucket)
        hits = sum(1 for r in bucket if r.get("direction_correct"))
        flow_regime_buckets[label] = {
            "eval_count": total,
            "direction_hits": hits,
            "direction_hit_rate": round(hits / total, 4) if total else None,
        }

    trading_dates = frame["date"].astype(str).str[:10].tolist()
    from trade_integrations.dataflows.index_research.prediction_miss_analysis import (
        enrich_eval_row_horizon,
    )

    exclude_cols = {"date", "close", "target", "realized_1d_pct"}
    horizon_feature_cols = [
        c
        for c in frame.columns
        if c not in exclude_cols and pd.api.types.is_numeric_dtype(frame[c])
    ]
    eval_rows = [
        enrich_eval_row_horizon(
            row,
            frame,
            horizon_feature_cols,
            horizon_days=horizon.days,
            trading_dates=trading_dates,
        )
        for row in eval_rows
    ]

    mae = float(np.mean(errors)) if errors else None
    hit_rate = directions_hit / directions_total if directions_total else None
    hybrid_hit_rate = hybrid_hits / hybrid_total if hybrid_total else None

    # Full-sample train metrics for comparison
    X, y, names = build_factor_matrix(frame.dropna(subset=["target"]), horizon)
    in_sample_artifact = None
    if X.size > 0:
        try:
            in_sample_artifact = train_macro_ridge(frame, horizon)
        except ImportError:
            pass

    scope = "hybrid" if hybrid_enabled and hybrid_total > 0 else "macro_only"
    direction_ci = bootstrap_direction_ci(eval_rows)
    sign_mag_scores = [float(r.get("sign_magnitude_score") or 0.0) for r in eval_rows]
    sign_mag_mean = float(np.mean(sign_mag_scores)) if sign_mag_scores else None

    report = {
        "status": "ok",
        "scope": scope,
        "eval_protocol": eval_protocol,
        "include_bottom_up": hybrid_enabled,
        "bottom_up_coverage": bottom_up_coverage,
        "as_of": datetime.now(timezone.utc).isoformat(),
        "ticker": "NIFTY",
        "horizon_days": horizon.days,
        "history_days_requested": days,
        "history_rows": len(frame),
        "history_start": str(frame["date"].iloc[0])[:10],
        "history_end": str(frame["date"].iloc[-1])[:10],
        "eval_count": len(eval_rows),
        "eval_step_days": eval_step,
        "metrics": {
            "mae_pct": round(mae, 4) if mae is not None else None,
            "direction_hit_rate": round(hit_rate, 4) if hit_rate is not None else None,
            "direction_hit_rate_walk_forward": round(hit_rate, 4) if hit_rate is not None else None,
            "macro_only_direction_hit_rate": round(hit_rate, 4) if hit_rate is not None else None,
            "hybrid_direction_hit_rate": round(hybrid_hit_rate, 4) if hybrid_hit_rate is not None else None,
            "hybrid_eval_count": hybrid_total,
            "regime_direction_hit_rates": regime_buckets,
            "flow_regime_direction_hit_rates": flow_regime_buckets,
            "in_sample_mae_pct": in_sample_artifact.mae if in_sample_artifact else None,
            "in_sample_r2": in_sample_artifact.r2_walk_forward if in_sample_artifact else None,
            "in_sample_direction_hit_rate": in_sample_artifact.direction_hit_rate_train_holdout
            if in_sample_artifact
            else None,
            "direction_bootstrap_ci": direction_ci,
            "sign_magnitude_score_mean": round(sign_mag_mean, 4) if sign_mag_mean is not None else None,
        },
        "factor_audit": audit_factor_coverage(frame),
        "factor_correlations": audit_factor_correlations(frame, horizon_days=horizon.days),
        "major_drawdowns": enrich_drawdowns(
            find_major_drawdowns(frame, threshold_1d_pct=-1.0, limit=15)
        ),
        "nifty_series": nifty_series,
        "daily_evaluations": eval_rows,
        "limitations": [
            "Macro-only backtest: per-stock news replay needs daily company_research archives (building via nightly snapshot job).",
            "Bottom-up constituent attribution replays only when archived company research exists for that date.",
            "FII/DII/PCR from NSE + Mr. Chartist; seeded continuity rows excluded.",
            "Constituent news on drawdown rows uses archived headlines when available.",
            "Event attribution uses calendar flags + factor day-over-day changes alongside index-level drivers.",
        ],
        "feature_names_used": names,
        "redundancy_prune": redundancy_audit(),
    }
    return report


def run_and_save_backtest(**kwargs: Any) -> dict[str, Any]:
    """Run walk-forward backtest and persist to hub."""
    report = run_walk_forward_backtest(**kwargs)
    if report.get("status") == "ok":
        save_backtest_report(report)
    return report
