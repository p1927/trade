"""Track scoreboard persistence."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from trade_integrations.context.hub import get_hub_dir
from trade_integrations.dataflows.index_research.prediction_algorithms.track_constants import (
    BACKTEST_COMBINER_IDS,
    BACKTEST_TRACK_IDS,
    CANONICAL_TRACK_IDS,
    COMBINER_THREE_TRACK_IDS,
    SCOREBOARD_SCHEMA_VERSION,
    TRACK_BACKTEST_ELIGIBLE,
    TRACK_IMPLEMENTATION_NOTES,
)
from trade_integrations.dataflows.index_research.views import classify_index_view


def scoreboard_path(ticker: str = "NIFTY") -> Path:
    return get_hub_dir() / ticker.strip().upper() / "index_research" / "track_scoreboard_latest.json"


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
    return enrich_track_metrics_from_daily(out)


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


def _view_hit(predicted_pct: float, actual_pct: float) -> bool:
    return classify_index_view(predicted_pct) == classify_index_view(actual_pct)


def summarize_track_metrics(
    eval_rows: list[dict[str, Any]],
    track_id: str,
    *,
    window: int | None = None,
    before_date: str | None = None,
) -> dict[str, Any]:
    preds = [r for r in eval_rows if r.get("track_id") == track_id]
    if before_date:
        preds = [r for r in preds if str(r.get("date") or "") < before_date]
    if window is not None and window > 0:
        preds = preds[-window:]
    if not preds:
        return {"track_id": track_id, "eval_count": 0}
    errors = [abs(float(r.get("error_pct") or 0.0)) for r in preds]
    dir_hits = sum(1 for r in preds if r.get("direction_hit"))
    if any(r.get("view_hit") is not None for r in preds):
        view_hits = sum(1 for r in preds if r.get("view_hit"))
    else:
        view_hits = sum(
            1
            for r in preds
            if _view_hit(float(r.get("predicted_pct") or 0.0), float(r.get("actual_pct") or 0.0))
        )
    total = len(preds)
    misses = total - dir_hits
    return {
        "track_id": track_id,
        "eval_count": total,
        "mae_pct": round(sum(errors) / total, 4),
        "direction_hit_rate": round(dir_hits / total, 4) if total else None,
        "view_hit_rate": round(view_hits / total, 4) if total else None,
        "direction_hit_count": dir_hits,
        "direction_miss_count": misses,
    }


def mae_by_track_from_scoreboard(
    scoreboard: dict[str, Any] | None,
    *,
    track_ids: tuple[str, ...] | list[str] | None = None,
    window: int | None = None,
    before_date: str | None = None,
) -> dict[str, float]:
    """Build MAE map for combiner weighting from scoreboard daily evaluations."""
    if not scoreboard:
        return {}
    daily = scoreboard.get("daily_evaluations") or []
    ids = list(track_ids or COMBINER_THREE_TRACK_IDS)
    out: dict[str, float] = {}
    for tid in ids:
        metrics = summarize_track_metrics(daily, tid, window=window, before_date=before_date)
        mae = metrics.get("mae_pct")
        out[tid] = float(mae) if mae is not None else 1.0
    return out


def enrich_track_metrics_from_daily(report: dict[str, Any]) -> dict[str, Any]:
    """Backfill hit/miss counts on cached scoreboards that predate direction_hit_count."""
    daily = report.get("daily_evaluations") or []
    if not daily:
        return report

    out = dict(report)

    def _fill(section_key: str, *, combiner_prefix: bool) -> None:
        section = dict(out.get(section_key) or {})
        for tid, row in section.items():
            if not isinstance(row, dict):
                continue
            lookup_id = f"combiner:{tid}" if combiner_prefix else tid
            if row.get("direction_hit_count") is not None and row.get("direction_miss_count") is not None:
                if row.get("view_hit_rate") is not None:
                    continue
            metrics = summarize_track_metrics(daily, lookup_id)
            row = dict(row)
            row["direction_hit_count"] = metrics.get("direction_hit_count")
            row["direction_miss_count"] = metrics.get("direction_miss_count")
            if row.get("direction_hit_rate") is None and metrics.get("direction_hit_rate") is not None:
                row["direction_hit_rate"] = metrics["direction_hit_rate"]
            if row.get("view_hit_rate") is None and metrics.get("view_hit_rate") is not None:
                row["view_hit_rate"] = metrics["view_hit_rate"]
            if row.get("mae_pct") is None and metrics.get("mae_pct") is not None:
                row["mae_pct"] = metrics["mae_pct"]
            if row.get("eval_count") in (None, 0) and metrics.get("eval_count"):
                row["eval_count"] = metrics["eval_count"]
            section[tid] = row
        out[section_key] = section

    _fill("tracks", combiner_prefix=False)
    _fill("combiners", combiner_prefix=True)
    return out
