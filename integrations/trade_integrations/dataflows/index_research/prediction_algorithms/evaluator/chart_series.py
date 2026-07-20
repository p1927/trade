"""Build chart-ready series from track walk-forward eval rows."""

from __future__ import annotations

from typing import Any

_TRACK_LABELS: dict[str, str] = {
    "quant_ridge": "Quant Ridge",
    "quant_ridge_no_overlay": "Quant Ridge (no overlay)",
    "macro_only": "Macro only",
    "macro_only_no_overlay": "Macro only (no overlay)",
    "bottom_up": "Bottom up",
    "scenario_anchor": "Scenario anchor",
    "event_overlay": "Event overlay",
    "naive_zero": "Naive zero",
    "naive_momentum": "Naive momentum",
    "debate_numeric": "Debate numeric",
    "headline_legacy": "Headline legacy",
    "combiner:quant_only": "Combiner: quant only",
    "combiner:equal_weight_2": "Combiner: equal (2)",
    "combiner:equal_weight_3": "Combiner: equal (3)",
    "combiner:inverse_mae_w6": "Combiner: inverse MAE (W6)",
    "combiner:inverse_mae_w12": "Combiner: inverse MAE (W12)",
    "combiner:shrinkage_50": "Combiner: shrinkage",
    "combiner:alignment_grid": "Combiner: alignment",
    "combiner:stress_conditional": "Combiner: stress",
    "combiner:fixed_legacy": "Combiner: fixed legacy",
}


def _label(track_id: str) -> str:
    return _TRACK_LABELS.get(track_id, track_id.replace("_", " ").title())


def build_track_chart_payload(
    eval_rows: list[dict[str, Any]],
    *,
    nifty_series: list[dict[str, Any]] | None = None,
    live_tracks: dict[str, Any] | None = None,
    live_as_of: str | None = None,
    live_spot: float | None = None,
    horizon_days: int = 14,
) -> dict[str, Any]:
    """Pivot OOS eval rows into multi-series chart data for UI."""
    by_date: dict[str, dict[str, Any]] = {}
    track_ids: set[str] = set()

    for row in eval_rows:
        day = str(row.get("date") or "")[:10]
        tid = str(row.get("track_id") or "")
        if not day or not tid:
            continue
        track_ids.add(tid)
        bucket = by_date.setdefault(
            day,
            {
                "date": day,
                "actual_pct": None,
                "close": row.get("close"),
                "tracks": {},
            },
        )
        if row.get("actual_pct") is not None:
            bucket["actual_pct"] = float(row["actual_pct"])
        if row.get("close") is not None:
            bucket["close"] = float(row["close"])
        bucket["tracks"][tid] = {
            "predicted_pct": float(row.get("predicted_pct") or 0.0),
            "error_pct": float(row.get("error_pct") or 0.0),
            "direction_hit": bool(row.get("direction_hit")),
        }

    dates = sorted(by_date.keys())
    actual_series = [
        {
            "date": d,
            "actual_pct": by_date[d].get("actual_pct"),
            "close": by_date[d].get("close"),
        }
        for d in dates
    ]

    primary_tracks = sorted(t for t in track_ids if not t.startswith("combiner:"))
    combiner_tracks = sorted(t for t in track_ids if t.startswith("combiner:"))

    track_series: list[dict[str, Any]] = []
    for tid in primary_tracks + combiner_tracks:
        points = []
        for d in dates:
            trow = by_date[d]["tracks"].get(tid)
            if not trow:
                continue
            points.append(
                {
                    "date": d,
                    "predicted_pct": trow["predicted_pct"],
                    "error_pct": trow["error_pct"],
                    "direction_hit": trow["direction_hit"],
                }
            )
        if points:
            track_series.append({"track_id": tid, "label": _label(tid), "points": points})

    live_point = None
    if live_tracks and live_as_of:
        day = str(live_as_of)[:10]
        preds = {
            tid: float((row or {}).get("expected_return_pct") or 0.0)
            for tid, row in live_tracks.items()
            if isinstance(row, dict) and row.get("available", True)
        }
        if preds:
            live_point = {
                "date": day,
                "spot": live_spot,
                "tracks": preds,
                "is_live": True,
            }

    close_by_date = {str(r.get("date") or "")[:10]: float(r["close"]) for r in (nifty_series or []) if r.get("close") is not None}
    eval_dates = set(dates)
    nifty_close = [
        {"date": d, "close": close_by_date[d]}
        for d in sorted(close_by_date.keys())
        if not eval_dates or d <= max(eval_dates) or d in eval_dates
    ]
    if live_point and live_point.get("spot") and live_point.get("date"):
        nifty_close.append({"date": live_point["date"], "close": float(live_point["spot"])})

    return {
        "horizon_days": horizon_days,
        "eval_dates": dates,
        "actual_series": actual_series,
        "track_series": track_series,
        "nifty_close_series": nifty_close[-400:],
        "live_point": live_point,
        "track_ids": list(primary_tracks + combiner_tracks),
    }
