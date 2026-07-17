"""Calibrate per-topic news shock magnitudes from reconciled impact ledger."""

from __future__ import annotations

import json
import statistics
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from trade_integrations.context.hub import get_hub_dir
from trade_integrations.dataflows.index_research.news_tags import tags_from_dict

_MIN_SAMPLE = 5
_SHRINK_CAP_N = 10
_DEFAULT_SHOCK_PCT = 8.0

_STATUS_WEIGHT = {"approved": 1.0, "partial": 0.5, "pending": 0.25}


def _calibration_path(ticker: str = "NIFTY") -> Path:
    return get_hub_dir() / ticker.strip().upper() / "index_research" / "news_shock_calibration.json"


def _primary_topic(record: dict[str, Any]) -> str:
    tags = tags_from_dict(record.get("tags"))
    if tags.topics:
        return tags.topics[0]
    tagged = record.get("tagged_factors") or []
    if tagged:
        factor = str(tagged[0].get("factor") or "")
        if "oil" in factor:
            return "oil"
        if "fii" in factor:
            return "fii"
        if factor == "repo_rate":
            return "rbi"
        if factor == "sp500":
            return "us_markets"
    return "index_sentiment"


def _record_weight(record: dict[str, Any]) -> float:
    status = str(record.get("verification_status") or "partial")
    return _STATUS_WEIGHT.get(status, 0.5)


def build_shock_calibration(*, ticker: str = "NIFTY") -> dict[str, Any]:
    """Aggregate reconciled stories into per-topic shock table."""
    from trade_integrations.hub_storage.verified_news_store import list_verified_records

    records = list_verified_records(limit=10000, ticker=ticker, include_rejected=False)
    buckets: dict[str, list[dict[str, float]]] = {}

    for record in records:
        actual = record.get("actual_impact") or record.get("actual") or {}
        predicted = record.get("predicted_impact") or record.get("predicted") or {}
        actual_ret = actual.get("return_pct")
        if actual_ret is None:
            continue
        topic = _primary_topic(record)
        weight = _record_weight(record)
        pred_ret = float(predicted.get("return_pct") or 0.0)
        buckets.setdefault(topic, []).append(
            {
                "actual_return_pct": float(actual_ret),
                "predicted_return_pct": pred_ret,
                "calibration_error": float(actual_ret) - pred_ret,
                "weight": weight,
            }
        )

    topics: dict[str, Any] = {}
    for topic, rows in sorted(buckets.items()):
        if not rows:
            continue
        actuals = [r["actual_return_pct"] for r in rows]
        preds = [r["predicted_return_pct"] for r in rows]
        errors = [r["calibration_error"] for r in rows]
        n = len(rows)
        shrink = min(1.0, n / _SHRINK_CAP_N)
        median_error = statistics.median(errors)
        topics[topic] = {
            "sample_count": n,
            "shrink_weight": round(shrink, 3),
            "median_actual_return_pct": round(statistics.median(actuals), 4),
            "median_predicted_return_pct": round(statistics.median(preds), 4),
            "median_calibration_error": round(median_error, 4),
            "calibrated_shock_pct": round(
                shrink * median_error + (1.0 - shrink) * 0.0,
                4,
            ),
            "overlay_eligible": n >= _MIN_SAMPLE,
        }

    reconciled_total = sum(t["sample_count"] for t in topics.values())
    return {
        "status": "ok",
        "as_of": datetime.now(timezone.utc).isoformat(),
        "ticker": ticker,
        "reconciled_total": reconciled_total,
        "topics": topics,
        "default_shock_pct": _DEFAULT_SHOCK_PCT,
        "min_sample": _MIN_SAMPLE,
    }


def save_shock_calibration(report: dict[str, Any], *, ticker: str = "NIFTY") -> Path:
    path = _calibration_path(ticker)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    return path


def load_shock_calibration(ticker: str = "NIFTY") -> dict[str, Any] | None:
    path = _calibration_path(ticker)
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def update_shock_calibration(*, ticker: str = "NIFTY") -> dict[str, Any]:
    report = build_shock_calibration(ticker=ticker)
    save_shock_calibration(report, ticker=ticker)
    return report


def calibrated_shock_pct_for_topic(topic: str, *, ticker: str = "NIFTY") -> float:
    table = load_shock_calibration(ticker) or {}
    entry = (table.get("topics") or {}).get(topic) or {}
    shock = entry.get("calibrated_shock_pct")
    if shock is not None and entry.get("overlay_eligible"):
        return float(shock)
    return _DEFAULT_SHOCK_PCT
