"""Counterfactual decomposition: T0 mapping vs horizon drift vs unexplained."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from trade_integrations.context.hub import get_hub_dir
from trade_integrations.dataflows.index_research.backtest_runner import (
    load_backtest_report,
    run_walk_forward_backtest,
)
from trade_integrations.dataflows.index_research.factor_matrix import MACRO_FACTOR_KEYS, build_factor_matrix
from trade_integrations.dataflows.index_research.horizon import resolve_horizon
from trade_integrations.dataflows.index_research.horizon_dates import resolve_maturity_trading_date
from trade_integrations.dataflows.index_research.predictor import (
    ModelArtifact,
    _MACRO_DELTA_CAP_PCT,
    _expand_poly,
    _macro_trust_weight,
    _predict_macro_delta,
    _scale_features,
    cap_macro_delta,
    train_macro_ridge,
)
from trade_integrations.dataflows.index_research.prediction_miss_analysis import (
    _row_factor_dict,
    factor_snapshot_at,
)
from trade_integrations.dataflows.index_research.sources.history_loader import load_aligned_factor_history

_MIN_TRAIN_ROWS = 45
_DEFAULT_EVAL_STEP = 5


def _counterfactual_path(ticker: str = "NIFTY") -> Path:
    return get_hub_dir() / ticker.strip().upper() / "index_research" / "counterfactual_latest.json"


def save_counterfactual_report(report: dict[str, Any], *, ticker: str = "NIFTY") -> Path:
    path = _counterfactual_path(ticker)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    return path


def load_counterfactual_report(ticker: str = "NIFTY") -> dict[str, Any] | None:
    path = _counterfactual_path(ticker)
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


def _factors_for_artifact(
    factors: dict[str, float],
    artifact: ModelArtifact,
) -> dict[str, float]:
    out: dict[str, float] = {}
    for name in artifact.feature_names:
        if name in factors:
            out[name] = float(factors[name])
    return out


def decompose_macro_prediction(
    factors: dict[str, float],
    artifact: ModelArtifact,
    *,
    horizon_profile: Any,
) -> tuple[float, float, list[dict[str, Any]]]:
    """Return (raw_macro, capped_macro, contributor rows) for one factor snapshot."""
    del horizon_profile
    macro_row = _factors_for_artifact(factors, artifact)
    raw = _predict_macro_delta(macro_row, resolve_horizon(14), artifact)
    trust = _macro_trust_weight(float(artifact.mae or 1.5))
    # _predict_macro_delta already applies trust; recover pre-trust for decomposition
    raw_untrusted = raw / trust if trust > 1e-9 else raw

    values: list[float] = []
    for name in artifact.feature_names:
        values.append(float(macro_row.get(name, 0.0)))
    raw_vec = np.array(values, dtype=float).reshape(1, -1)
    if artifact.feature_means and artifact.feature_stds:
        scaled = _scale_features(raw_vec, artifact.feature_means, artifact.feature_stds)
    else:
        scaled = raw_vec
    expanded, poly_names = _expand_poly(scaled, artifact.feature_names, artifact.poly_degree)

    contributors: list[dict[str, Any]] = []
    for name, val in zip(poly_names, expanded.flatten(), strict=False):
        coef = float(artifact.coefficients.get(name, 0.0))
        contrib = coef * float(val) * trust
        if abs(contrib) < 1e-9:
            continue
        contributors.append(
            {
                "term": name,
                "coefficient": round(coef, 6),
                "poly_value": round(float(val), 6),
                "contribution_pct": round(contrib, 4),
            }
        )
    contributors.sort(key=lambda r: abs(r["contribution_pct"]), reverse=True)

    capped = cap_macro_delta(raw)
    return round(raw_untrusted, 4), round(capped, 4), contributors


def classify_counterfactual_row(
    *,
    predicted_t0: float,
    actual: float,
    explained_by_drift: float,
    residual: float,
    macro_raw: float,
    macro_capped: float,
    missing_t0: list[str],
) -> str:
    if len(missing_t0) >= 2:
        return "data_gap_T0"
    if abs(macro_raw) > _MACRO_DELTA_CAP_PCT + 0.01 and abs(macro_raw - macro_capped) > 0.5:
        if (predicted_t0 >= 0) != (actual >= 0):
            return "cap_artifact"
    if (predicted_t0 >= 0) != (actual >= 0):
        if abs(explained_by_drift) >= 0.5 * abs(residual):
            return "drift_dominant"
        return "mapping_error_T0"
    if abs(explained_by_drift) >= 0.5 * abs(residual):
        return "drift_dominant"
    return "magnitude_error"


def analyze_eval_counterfactual(
    eval_row: dict[str, Any],
    *,
    frame: pd.DataFrame,
    feature_cols: list[str],
    train_frame: pd.DataFrame,
    horizon_days: int,
    trading_dates: list[str],
) -> dict[str, Any]:
    """Decompose one walk-forward eval row using artifact trained only before T0."""
    horizon = resolve_horizon(horizon_days)
    pred_day = str(eval_row.get("date") or "")[:10]
    maturity = resolve_maturity_trading_date(pred_day, horizon_days, trading_dates)
    actual = float(eval_row.get("actual_forward_return_pct") or 0)

    try:
        artifact = train_macro_ridge(train_frame, horizon)
    except ImportError:
        return {"prediction_date": pred_day, "status": "error", "message": "sklearn missing"}

    t0_factors = factor_snapshot_at(pred_day, frame, feature_cols, keys=MACRO_FACTOR_KEYS)
    t1_factors = factor_snapshot_at(maturity or pred_day, frame, feature_cols, keys=MACRO_FACTOR_KEYS)
    missing_t0 = [k for k in MACRO_FACTOR_KEYS if k not in t0_factors]

    _, predicted_capped, t0_contribs = decompose_macro_prediction(t0_factors, artifact, horizon_profile=horizon)
    raw_t0, _, _ = decompose_macro_prediction(t0_factors, artifact, horizon_profile=horizon)
    _, _, t1_contribs = decompose_macro_prediction(t1_factors, artifact, horizon_profile=horizon)

    # Drift via contributor difference on matching terms
    t0_map = {c["term"]: c["contribution_pct"] for c in t0_contribs}
    t1_map = {c["term"]: c["contribution_pct"] for c in t1_contribs}
    drift_contribs: list[dict[str, Any]] = []
    explained_by_drift = 0.0
    for term in set(t0_map) | set(t1_map):
        delta = float(t1_map.get(term, 0.0)) - float(t0_map.get(term, 0.0))
        if abs(delta) < 1e-9:
            continue
        drift_contribs.append({"term": term, "delta_contribution_pct": round(delta, 4)})
        explained_by_drift += delta
    drift_contribs.sort(key=lambda r: abs(r["delta_contribution_pct"]), reverse=True)

    predicted_t0 = float(eval_row.get("predicted_return_pct") or predicted_capped)
    residual = actual - predicted_t0
    unexplained = residual - explained_by_drift
    direction_correct = bool(eval_row.get("direction_correct"))

    classification = None
    if not direction_correct:
        classification = classify_counterfactual_row(
            predicted_t0=predicted_t0,
            actual=actual,
            explained_by_drift=explained_by_drift,
            residual=residual,
            macro_raw=float(eval_row.get("macro_raw_pct") or raw_t0),
            macro_capped=predicted_t0,
            missing_t0=missing_t0,
        )

    return {
        "prediction_date": pred_day,
        "maturity_date": maturity,
        "predicted_t0_pct": round(predicted_t0, 4),
        "actual_return_pct": round(actual, 4),
        "direction_correct": direction_correct,
        "residual_pct": round(residual, 4),
        "explained_by_drift_pct": round(explained_by_drift, 4),
        "unexplained_pct": round(unexplained, 4),
        "reconciliation_check_pct": round(predicted_t0 + explained_by_drift + unexplained, 4),
        "t0_contributions": t0_contribs[:12],
        "drift_contributions": drift_contribs[:12],
        "classification": classification,
        "missing_factors_t0": missing_t0[:8],
    }


def run_counterfactual_analysis(
    *,
    days: int = 365,
    horizon_days: int = 14,
    min_train_rows: int = _MIN_TRAIN_ROWS,
    eval_step: int = _DEFAULT_EVAL_STEP,
    ticker: str = "NIFTY",
    backtest_report: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Run counterfactual decomposition for all walk-forward eval rows."""
    report = backtest_report or load_backtest_report(ticker)
    if report is None or report.get("status") != "ok":
        report = run_walk_forward_backtest(
            days=days,
            horizon_days=horizon_days,
            min_train_rows=min_train_rows,
            eval_step=eval_step,
        )
    if report.get("status") != "ok":
        return {"status": "error", "message": report.get("message") or "backtest failed"}

    frame = load_aligned_factor_history(days=days)
    if frame.empty:
        return {"status": "error", "message": "no aligned history"}

    frame = frame.sort_values("date").reset_index(drop=True)
    frame["target"] = (frame["close"].astype(float).shift(-horizon_days) - frame["close"]) / frame[
        "close"
    ] * 100.0
    feature_cols = _feature_columns(frame)
    trading_dates = frame["date"].astype(str).str[:10].tolist()

    rows: list[dict[str, Any]] = []
    eval_rows = report.get("daily_evaluations") or []
    for eval_row in eval_rows:
        pred_day = str(eval_row.get("date") or "")[:10]
        try:
            idx = trading_dates.index(pred_day)
        except ValueError:
            continue
        if idx < min_train_rows:
            continue
        train = frame.iloc[:idx].copy()
        rows.append(
            analyze_eval_counterfactual(
                eval_row,
                frame=frame,
                feature_cols=feature_cols,
                train_frame=train,
                horizon_days=horizon_days,
                trading_dates=trading_dates,
            )
        )

    misses = [r for r in rows if r.get("direction_correct") is False]
    mapping_errors = [r for r in misses if r.get("classification") == "mapping_error_T0"]
    drift_dominant = [r for r in misses if r.get("classification") == "drift_dominant"]

    drift_totals: dict[str, float] = {}
    for row in misses:
        for d in row.get("drift_contributions") or []:
            term = str(d.get("term") or "")
            drift_totals[term] = drift_totals.get(term, 0.0) + abs(float(d.get("delta_contribution_pct") or 0))
    top_drift = sorted(
        [{"term": k, "abs_drift_sum": round(v, 4)} for k, v in drift_totals.items()],
        key=lambda x: x["abs_drift_sum"],
        reverse=True,
    )[:10]

    return {
        "status": "ok",
        "as_of": datetime.now(timezone.utc).isoformat(),
        "ticker": ticker,
        "horizon_days": horizon_days,
        "eval_count": len(rows),
        "summary": {
            "direction_hit_rate": (report.get("metrics") or {}).get("direction_hit_rate"),
            "miss_count": len(misses),
            "mapping_error_count": len(mapping_errors),
            "drift_dominant_count": len(drift_dominant),
            "cap_artifact_count": sum(1 for r in misses if r.get("classification") == "cap_artifact"),
            "top_drift_factors": top_drift,
        },
        "rows": rows,
        "misses": misses,
    }


def run_and_save_counterfactual(**kwargs: Any) -> dict[str, Any]:
    report = run_counterfactual_analysis(**kwargs)
    if report.get("status") == "ok":
        save_counterfactual_report(report, ticker=str(kwargs.get("ticker") or "NIFTY"))
    return report


def analyze_ledger_counterfactual(
    ledger_rows: list[dict[str, Any]],
    *,
    horizon_days: int = 14,
) -> list[dict[str, Any]]:
    """Counterfactual decomposition for matured ledger misses using stored T0 factors."""
    if not ledger_rows:
        return []
    frame = load_aligned_factor_history(days=400)
    if frame.empty:
        return []
    feature_cols = [
        c
        for c in frame.columns
        if c not in {"date", "close", "target", "realized_1d_pct"}
        and pd.api.types.is_numeric_dtype(frame[c])
    ]
    trading_dates = frame["date"].astype(str).str[:10].tolist()
    horizon = resolve_horizon(horizon_days)
    out: list[dict[str, Any]] = []

    for row in ledger_rows:
        meta = row.get("metadata") or {}
        factors_t0 = meta.get("global_factors") or {}
        pred_day = str(row.get("predicted_at") or "")[:10]
        if not pred_day or not factors_t0:
            continue
        try:
            idx = trading_dates.index(pred_day)
        except ValueError:
            continue
        if idx < _MIN_TRAIN_ROWS:
            continue
        train = frame.iloc[:idx].copy()
        try:
            artifact = train_macro_ridge(train, horizon)
        except (ValueError, ImportError):
            continue
        predicted = float(row.get("expected_return_pct") or 0)
        actual = float(row.get("actual_return_pct") or 0)
        maturity = resolve_maturity_trading_date(pred_day, horizon_days, trading_dates)
        t1_factors = factor_snapshot_at(maturity or pred_day, frame, feature_cols, keys=MACRO_FACTOR_KEYS)
        _, predicted_capped, t0_contribs = decompose_macro_prediction(
            {k: float(v) for k, v in factors_t0.items()},
            artifact,
            horizon_profile=horizon,
        )
        _, _, t1_contribs = decompose_macro_prediction(t1_factors, artifact, horizon_profile=horizon)
        t0_map = {c["term"]: c["contribution_pct"] for c in t0_contribs}
        t1_map = {c["term"]: c["contribution_pct"] for c in t1_contribs}
        explained_by_drift = 0.0
        drift_contribs: list[dict[str, Any]] = []
        for term in set(t0_map) | set(t1_map):
            delta = float(t1_map.get(term, 0.0)) - float(t0_map.get(term, 0.0))
            if abs(delta) < 1e-9:
                continue
            drift_contribs.append({"term": term, "delta_contribution_pct": round(delta, 4)})
            explained_by_drift += delta
        residual = actual - predicted
        out.append(
            {
                "prediction_date": pred_day,
                "maturity_date": maturity,
                "predicted_t0_pct": round(predicted, 4),
                "actual_return_pct": round(actual, 4),
                "residual_pct": round(residual, 4),
                "explained_by_drift_pct": round(explained_by_drift, 4),
                "unexplained_pct": round(residual - explained_by_drift, 4),
                "t0_contributions": t0_contribs[:8],
                "drift_contributions": drift_contribs[:8],
                "classification": classify_counterfactual_row(
                    predicted_t0=predicted,
                    actual=actual,
                    explained_by_drift=explained_by_drift,
                    residual=residual,
                    macro_raw=predicted_capped,
                    macro_capped=predicted,
                    missing_t0=[k for k in MACRO_FACTOR_KEYS if k not in factors_t0],
                )
                if row.get("direction_correct") is False
                else None,
            }
        )
    return out
