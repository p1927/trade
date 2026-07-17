"""Track scoreboard persistence."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from trade_integrations.dataflows.index_research.prediction_algorithms.promotion import scoreboard_path
from trade_integrations.dataflows.index_research.prediction_algorithms.track_constants import (
    BACKTEST_COMBINER_IDS,
    BACKTEST_TRACK_IDS,
    CANONICAL_TRACK_IDS,
    SCOREBOARD_SCHEMA_VERSION,
    TRACK_BACKTEST_ELIGIBLE,
    TRACK_IMPLEMENTATION_NOTES,
)


def scoreboard_needs_refresh(
    report: dict[str, Any] | None,
    *,
    horizon_days: int | None = None,
    history_days: int | None = None,
) -> bool:
    """True when cached scoreboard is missing, stale, or mismatched to request params."""
    if not report:
        return True
    if int(report.get("schema_version") or 0) < SCOREBOARD_SCHEMA_VERSION:
        return True
    if int(report.get("eval_count") or 0) <= 0:
        return True
    tracks = report.get("tracks") or {}
    if len(tracks) < len(BACKTEST_TRACK_IDS):
        return True
    if horizon_days is not None and int(report.get("horizon_days") or 0) != int(horizon_days):
        return True
    if history_days is not None and int(report.get("history_days") or 0) < int(history_days):
        return True
    return False


def normalize_scoreboard_report(report: dict[str, Any]) -> dict[str, Any]:
    """Ensure all canonical tracks/combiners appear in summary tables."""
    out = dict(report)
    tracks = dict(out.get("tracks") or {})
    for tid in CANONICAL_TRACK_IDS:
        tracks.setdefault(tid, {"track_id": tid, "eval_count": 0, "backtest_eligible": TRACK_BACKTEST_ELIGIBLE.get(tid, False)})
    for tid, row in tracks.items():
        row.setdefault("backtest_eligible", TRACK_BACKTEST_ELIGIBLE.get(tid, False))
    out["tracks"] = tracks

    combiners = dict(out.get("combiners") or {})
    for cid in BACKTEST_COMBINER_IDS:
        combiners.setdefault(cid, {"track_id": cid, "eval_count": 0})
    out["combiners"] = combiners

    out["track_catalog"] = {
        tid: {
            "label": tid.replace("_", " "),
            "implementation": TRACK_IMPLEMENTATION_NOTES.get(tid, ""),
            "backtest_eligible": TRACK_BACKTEST_ELIGIBLE.get(tid, False),
            "metrics": tracks.get(tid) or {},
        }
        for tid in CANONICAL_TRACK_IDS
    }
    out.setdefault("schema_version", SCOREBOARD_SCHEMA_VERSION)
    return out


def save_scoreboard(ticker: str, report: dict[str, Any]) -> Path:
    path = scoreboard_path(ticker)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = normalize_scoreboard_report(dict(report))
    payload.setdefault("as_of", datetime.now(timezone.utc).isoformat())
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return path


def load_scoreboard(ticker: str = "NIFTY") -> dict[str, Any] | None:
    path = scoreboard_path(ticker)
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def summarize_track_metrics(eval_rows: list[dict[str, Any]], track_id: str) -> dict[str, Any]:
    preds = [r for r in eval_rows if r.get("track_id") == track_id]
    if not preds:
        return {"track_id": track_id, "eval_count": 0}
    errors = [abs(float(r.get("error_pct") or 0.0)) for r in preds]
    hits = sum(1 for r in preds if r.get("direction_hit"))
    total = len(preds)
    return {
        "track_id": track_id,
        "eval_count": total,
        "mae_pct": round(sum(errors) / total, 4),
        "direction_hit_rate": round(hits / total, 4) if total else None,
    }
