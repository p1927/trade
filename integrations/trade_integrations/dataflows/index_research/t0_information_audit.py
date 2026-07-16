"""T0 information audit: knowable vs unknowable miss tags."""

from __future__ import annotations

import json
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from trade_integrations.context.hub import get_hub_dir
from trade_integrations.dataflows.index_research.backtest_runner import (
    _calendar_events_for_date,
    load_backtest_report,
)
from trade_integrations.dataflows.index_research.causal_attribution import _fetch_index_headlines
from trade_integrations.dataflows.index_research.factor_matrix import MACRO_FACTOR_KEYS
from trade_integrations.dataflows.index_research.prediction_counterfactual import (
    load_counterfactual_report,
)
from trade_integrations.dataflows.index_research.prediction_miss_analysis import (
    factor_snapshot_at,
    load_miss_analysis_report,
)

_MISS_CATEGORY_TO_CF = {
    "cap_saturation": "cap_artifact",
    "event_gap": "drift_dominant",
    "mapping_error": "mapping_error_T0",
}
from trade_integrations.dataflows.index_research.scenarios import build_index_scenarios
from trade_integrations.dataflows.index_research.sources.history_loader import load_aligned_factor_history

_EVENT_KEYWORDS = {
    "oil": ("oil", "crude", "brent", "opec", "energy"),
    "war": ("war", "conflict", "geopolit", "missile", "strike"),
    "rbi": ("rbi", "repo", "rate hike", "rate cut", "monetary policy"),
}


def _audit_path(ticker: str = "NIFTY") -> Path:
    return get_hub_dir() / ticker.strip().upper() / "index_research" / "t0_information_audit.json"


def save_t0_audit_report(report: dict[str, Any], *, ticker: str = "NIFTY") -> Path:
    path = _audit_path(ticker)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    return path


def load_t0_audit_report(ticker: str = "NIFTY") -> dict[str, Any] | None:
    path = _audit_path(ticker)
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _headline_tags(headlines: list[dict[str, str]]) -> set[str]:
    tags: set[str] = set()
    text = " ".join(
        str(h.get("title") or h.get("headline") or "").lower() for h in headlines
    )
    for tag, keywords in _EVENT_KEYWORDS.items():
        if any(kw in text for kw in keywords):
            tags.add(tag)
    return tags


def _headline_tags_from_hub(headlines: list[dict[str, Any]]) -> set[str]:
    """Union hub tags.topics with title-keyword fallback for RSS-only rows."""
    from trade_integrations.dataflows.index_research.news_tags import topics_from_record

    tags: set[str] = set()
    title_only: list[dict[str, str]] = []
    for headline in headlines:
        if headline.get("tags"):
            tags.update(topics_from_record(headline))
        else:
            title_only.append(headline)
    if title_only:
        tags.update(_headline_tags(title_only))
    return tags


def _global_flags(factors: dict[str, float]) -> dict[str, Any]:
    return {
        "is_results_season": bool(factors.get("is_results_season")),
        "is_budget_week": bool(factors.get("is_budget_week")),
        "days_to_monthly_expiry": factors.get("days_to_monthly_expiry"),
    }


def classify_t0_information(
    *,
    prediction_date: str,
    headlines_t0: list[dict[str, str]],
    factors_t0: dict[str, float],
    material_move: bool = True,
    counterfactual_class: str | None = None,
) -> str:
    """Tag miss as unknowable_future_shock, knowable_missing_feature, or knowable_ignored."""
    if counterfactual_class == "mapping_error_T0":
        return "knowable_missing_feature"
    if counterfactual_class == "drift_dominant":
        return "knowable_ignored"
    if counterfactual_class == "cap_artifact":
        return "knowable_ignored"

    tags = _headline_tags_from_hub(headlines_t0)
    has_oil_feature = "oil_brent" in factors_t0 or "oil_wti" in factors_t0
    has_flow_feature = "fii_net_5d" in factors_t0 or "dii_net_5d" in factors_t0

    if tags and not material_move:
        return "knowable_missing_feature" if not has_oil_feature and "oil" in tags else "knowable_ignored"

    if tags:
        if ("oil" in tags or "war" in tags) and not has_oil_feature:
            return "knowable_missing_feature"
        if "rbi" in tags and "repo_rate" not in factors_t0:
            return "knowable_missing_feature"
        if tags and has_flow_feature:
            dii = factors_t0.get("dii_net_5d")
            if dii is not None and abs(dii) < 1e-9:
                return "knowable_ignored"

    if not tags and material_move:
        return "unknowable_future_shock"
    if tags:
        return "knowable_missing_feature"
    return "unknowable_future_shock"


def audit_eval_row(
    eval_row: dict[str, Any],
    *,
    frame,
    feature_cols: list[str],
) -> dict[str, Any]:
    pred_day = str(eval_row.get("prediction_date") or eval_row.get("date") or "")[:10]
    headlines_t0: list[dict[str, str]] = []
    try:
        from trade_integrations.dataflows.news_hub_bridge import (
            headlines_for_prediction_date,
            to_headline_dict,
        )

        for item in headlines_for_prediction_date(pred_day, lookback_days=7, limit=12, ingest_if_missing=True):
            row = to_headline_dict(item)
            headlines_t0.append(
                {
                    "title": row["title"],
                    "source": row["source"],
                    "summary": row["summary"],
                    "tags": row.get("tags") or {},
                }
            )
    except Exception:
        pass
    if not headlines_t0:
        headlines_t0 = _fetch_index_headlines(pred_day)
    factors_t0 = factor_snapshot_at(pred_day, frame, feature_cols, keys=MACRO_FACTOR_KEYS)
    try:
        as_of = date.fromisoformat(pred_day)
    except ValueError:
        as_of = date.today()

    calendar = _calendar_events_for_date(as_of)
    scenarios: list[dict[str, Any]] = []
    spot = float(eval_row.get("spot") or 0)
    try:
        scenarios = build_index_scenarios(
            [],
            factors_t0,
            spot=spot or 1.0,
            horizon_days=int(eval_row.get("horizon_days") or 14),
        ) or []
    except Exception:
        scenarios = []

    actual = float(eval_row.get("actual_forward_return_pct") or 0)
    predicted = float(eval_row.get("predicted_return_pct") or 0)
    material = abs(actual) >= 1.0 or abs(actual - predicted) >= 1.5

    counterfactual_class = str(
        eval_row.get("counterfactual_class")
        or eval_row.get("classification")
        or _MISS_CATEGORY_TO_CF.get(str(eval_row.get("miss_category") or ""), "")
        or eval_row.get("miss_class")
        or ""
    )

    tag = classify_t0_information(
        prediction_date=pred_day,
        headlines_t0=headlines_t0,
        factors_t0=factors_t0,
        material_move=material,
        counterfactual_class=counterfactual_class or None,
    )

    return {
        "prediction_date": pred_day,
        "direction_correct": eval_row.get("direction_correct"),
        "predicted_return_pct": round(predicted, 4),
        "actual_return_pct": round(actual, 4),
        "t0_information_tag": tag,
        "counterfactual_class": counterfactual_class or None,
        "headline_tags_t0": sorted(_headline_tags_from_hub(headlines_t0)),
        "headlines_t0_count": len(headlines_t0),
        "calendar_events_t0": calendar,
        "global_flags_t0": _global_flags(factors_t0),
        "scenario_count_t0": len(scenarios),
        "missing_factors_t0": [k for k in MACRO_FACTOR_KEYS if k not in factors_t0][:8],
    }


def run_t0_information_audit(
    *,
    days: int = 365,
    horizon_days: int = 14,
    ticker: str = "NIFTY",
) -> dict[str, Any]:
    miss_report = load_miss_analysis_report(ticker)
    backtest = load_backtest_report(ticker)
    counterfactual = load_counterfactual_report(ticker)
    cf_by_date = {
        str(row.get("prediction_date") or "")[:10]: str(row.get("classification") or "")
        for row in (counterfactual or {}).get("rows") or []
        if row.get("classification")
    }
    eval_rows = (miss_report or {}).get("misses") or []
    if not eval_rows and backtest:
        eval_rows = [
            r for r in (backtest.get("daily_evaluations") or []) if r.get("direction_correct") is False
        ]
    enriched_rows: list[dict[str, Any]] = []
    for row in eval_rows:
        merged = dict(row)
        pred_day = str(row.get("prediction_date") or row.get("date") or "")[:10]
        if pred_day and not merged.get("counterfactual_class"):
            merged["counterfactual_class"] = cf_by_date.get(pred_day) or merged.get("counterfactual_class")
        enriched_rows.append(merged)
    eval_rows = enriched_rows

    frame = load_aligned_factor_history(days=days)
    if frame.empty:
        return {"status": "error", "message": "no aligned history"}

    feature_cols = [
        c
        for c in frame.columns
        if c not in {"date", "close", "target", "realized_1d_pct"}
        and pd.api.types.is_numeric_dtype(frame[c])
    ]

    rows = [audit_eval_row(row, frame=frame, feature_cols=feature_cols) for row in eval_rows]
    tag_counts: dict[str, int] = {}
    for row in rows:
        tag = str(row.get("t0_information_tag") or "unknown")
        tag_counts[tag] = tag_counts.get(tag, 0) + 1

    return {
        "status": "ok",
        "as_of": datetime.now(timezone.utc).isoformat(),
        "ticker": ticker,
        "horizon_days": horizon_days,
        "miss_count": len(rows),
        "tag_counts": tag_counts,
        "rows": rows,
    }


def run_and_save_t0_audit(**kwargs: Any) -> dict[str, Any]:
    report = run_t0_information_audit(**kwargs)
    if report.get("status") == "ok":
        save_t0_audit_report(report, ticker=str(kwargs.get("ticker") or "NIFTY"))
    return report
