"""Combiner promotion gates (+3 pp view hit vs quant, stability checks)."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from trade_integrations.dataflows.index_research.prediction_algorithms.combiners._math import (
    select_alignment_lambda,
)
from trade_integrations.dataflows.index_research.prediction_algorithms.config import default_combiner_id
from trade_integrations.dataflows.index_research.prediction_algorithms.evaluator.scoreboard import (
    load_scoreboard,
    mae_by_track_from_scoreboard,
)
from trade_integrations.dataflows.index_research.prediction_algorithms.track_constants import (
    COMBINER_THREE_TRACK_IDS,
    INVERSE_MAE_WINDOWS,
)

_MIN_EVAL_COUNT = 60
_VIEW_MARGIN = 0.03
_WEIGHT_STABILITY_MAX_STD = 0.25
_CONSECUTIVE_RUNS_REQUIRED = 2
_WEIGHT_STABILITY_COMBINERS = frozenset(
    {"inverse_mae_w6", "inverse_mae_w12", "shrinkage_50", "alignment_grid"},
)


def _combiner_needs_weight_stability(combiner_id: str) -> bool:
    return combiner_id in _WEIGHT_STABILITY_COMBINERS


def _weight_stability_ok(weight_history: list[dict[str, Any]], combiner_id: str) -> bool:
    """True when per-track weight std across last 3 snapshots is below threshold."""
    entries = [e for e in weight_history if e.get("combiner_id") == combiner_id]
    if len(entries) < 2:
        return False
    recent = entries[-3:]
    track_ids: set[str] = set()
    for entry in recent:
        track_ids.update((entry.get("weights") or {}).keys())
    for tid in track_ids:
        weights = [float((entry.get("weights") or {}).get(tid) or 0.0) for entry in recent]
        if len(weights) < 2:
            continue
        mean = sum(weights) / len(weights)
        var = sum((w - mean) ** 2 for w in weights) / len(weights)
        if var**0.5 > _WEIGHT_STABILITY_MAX_STD:
            return False
    return True


def _append_promotion_run_history(scoreboard: dict[str, Any], raw_promoted: list[str]) -> list[dict[str, Any]]:
    history = list(scoreboard.get("promotion_run_history") or [])
    history.append(
        {
            "as_of": datetime.now(timezone.utc).isoformat(),
            "promoted": list(raw_promoted),
        }
    )
    return history[-10:]


def _stable_from_history(scoreboard: dict[str, Any]) -> list[str]:
    """Combiners promoted in all of the last N distinct scoreboard runs."""
    history = scoreboard.get("promotion_run_history") or []
    if len(history) < _CONSECUTIVE_RUNS_REQUIRED:
        return []

    recent = history[-_CONSECUTIVE_RUNS_REQUIRED:]
    promoted_sets = [set(run.get("promoted") or []) for run in recent]
    if not promoted_sets:
        return []

    candidates = set.intersection(*promoted_sets)
    weight_hist = scoreboard.get("combiner_weight_history") or []
    combiners = scoreboard.get("combiners") or {}
    stable: list[str] = []
    for cid in candidates:
        if _combiner_needs_weight_stability(cid):
            if not weight_hist or not _weight_stability_ok(weight_hist, cid):
                continue
        stable.append(cid)
    stable.sort(key=lambda c: (-float(combiners.get(c, {}).get("view_hit_rate") or 0.0), c))
    return stable


def evaluate_promotion(scoreboard: dict[str, Any]) -> dict[str, Any]:
    """Return promotion verdict for each combiner vs quant_ridge (view-aligned metric)."""
    tracks = scoreboard.get("tracks") or {}
    combiners = scoreboard.get("combiners") or {}
    quant = tracks.get("quant_ridge") or {}
    if quant.get("backtest_eligible") is False:
        quant = {}
    quant_view = float(quant.get("view_hit_rate") or quant.get("direction_hit_rate") or 0.0)
    eval_count = int(scoreboard.get("eval_count") or quant.get("eval_count") or 0)

    verdicts: dict[str, Any] = {}
    equal = combiners.get("equal_weight_2") or {}
    equal_view = float(equal.get("view_hit_rate") or equal.get("direction_hit_rate") or 0.0)

    raw_promoted: list[str] = []
    for cid, row in combiners.items():
        if cid == "quant_only":
            continue
        hit = float(row.get("view_hit_rate") or row.get("direction_hit_rate") or 0.0)
        dir_hit = float(row.get("direction_hit_rate") or 0.0)
        passes = (
            eval_count >= _MIN_EVAL_COUNT
            and hit >= quant_view + _VIEW_MARGIN
            and hit >= equal_view
        )
        verdicts[cid] = {
            "promoted": passes,
            "view_hit_rate": hit,
            "direction_hit_rate": dir_hit,
            "delta_vs_quant_pp": round((hit - quant_view) * 100, 2),
            "eval_count": eval_count,
            "insufficient_evidence": eval_count < _MIN_EVAL_COUNT,
        }
        if passes:
            raw_promoted.append(cid)

    raw_promoted.sort(
        key=lambda c: (-float(verdicts[c].get("view_hit_rate") or 0.0), c),
    )
    stable = _stable_from_history(scoreboard)

    return {
        "eval_count": eval_count,
        "quant_view_hit_rate": quant_view,
        "quant_direction_hit_rate": float(quant.get("direction_hit_rate") or 0.0),
        "verdicts": verdicts,
        "promoted_combiners": stable,
        "raw_promoted_combiners": raw_promoted,
        "auto_promote_allowed": bool(stable) and eval_count >= _MIN_EVAL_COUNT,
        "min_eval_count_required": _MIN_EVAL_COUNT,
        "consecutive_runs_required": _CONSECUTIVE_RUNS_REQUIRED,
    }


def finalize_scoreboard_promotion(report: dict[str, Any], *, ticker: str = "NIFTY") -> dict[str, Any]:
    """Attach promotion verdict + run history before persisting scoreboard."""
    out = dict(report)
    prev = load_scoreboard(ticker)
    if prev and prev.get("promotion_run_history"):
        out["promotion_run_history"] = list(prev["promotion_run_history"])

    interim = evaluate_promotion(out)
    raw = interim.get("raw_promoted_combiners") or []
    out["promotion_run_history"] = _append_promotion_run_history(out, raw)
    promo = evaluate_promotion(out)
    out["promotion"] = promo
    return out


def resolve_combiner_runtime_kwargs(
    combiner_id: str,
    *,
    ticker: str = "NIFTY",
    as_of_day: str | None = None,
) -> dict[str, Any]:
    """Scoreboard-informed kwargs for live combiner runs."""
    board = load_scoreboard(ticker)
    if not board:
        return {}
    kwargs: dict[str, Any] = {}
    window = INVERSE_MAE_WINDOWS.get(combiner_id)
    if window is not None or combiner_id == "shrinkage_50":
        w = window or INVERSE_MAE_WINDOWS.get("inverse_mae_w6", 6)
        kwargs["mae_by_track"] = mae_by_track_from_scoreboard(
            board,
            track_ids=COMBINER_THREE_TRACK_IDS,
            window=w,
            before_date=as_of_day,
        )
    if combiner_id == "alignment_grid":
        daily = board.get("daily_evaluations") or []
        kwargs["lam"] = select_alignment_lambda(daily, before_date=as_of_day)
    return kwargs


def resolve_active_combiner(*, default: str | None = None, ticker: str = "NIFTY") -> str:
    """Pick combiner: env default, or auto from stable scoreboard promotion."""
    env = (default or default_combiner_id()).strip()
    if env != "auto":
        return env or "quant_only"

    board = load_scoreboard(ticker)
    if not board:
        return "quant_only"

    promo = evaluate_promotion(board)
    if not promo.get("auto_promote_allowed"):
        return "quant_only"

    promoted = promo.get("promoted_combiners") or []
    if promoted:
        return str(promoted[0])
    return "quant_only"


def enrich_scoreboard_with_live(
    report: dict[str, Any],
    *,
    ticker: str = "NIFTY",
) -> dict[str, Any]:
    """Attach live forecast_tracks + refresh chart live point from hub."""
    report = dict(report)
    try:
        from trade_integrations.context.hub import load_index_research_json
        from trade_integrations.dataflows.index_research.prediction_algorithms.evaluator.chart_series import (
            build_track_chart_payload,
        )

        doc = load_index_research_json(ticker.strip().upper())
        if doc is None:
            report["live_enrichment_note"] = "hub_index_research_unavailable"
            return report
        prediction = doc.prediction or {}
        live_tracks = prediction.get("forecast_tracks") or {}
        as_of = doc.as_of.isoformat() if hasattr(doc.as_of, "isoformat") else str(doc.as_of)
        spot = float(doc.spot or 0.0) if doc.spot else None
        report["live"] = {
            "as_of": as_of,
            "spot": spot,
            "forecast_tracks": live_tracks,
            "cause_stress_index": prediction.get("cause_stress_index"),
            "cause_stress_label": prediction.get("cause_stress_label"),
            "active_combiner": prediction.get("active_combiner"),
            "headline_source": prediction.get("headline_source"),
            "combiner_preview": prediction.get("combiner_preview"),
            "forecast_lab_error": prediction.get("forecast_lab_error"),
        }
        eval_rows = list(report.get("daily_evaluations") or [])
        report["chart"] = build_track_chart_payload(
            eval_rows,
            nifty_series=report.get("nifty_series"),
            live_tracks=live_tracks if isinstance(live_tracks, dict) else None,
            live_as_of=as_of,
            live_spot=spot,
            horizon_days=int(report.get("horizon_days") or 14),
        )
    except Exception as exc:
        import logging

        logging.getLogger(__name__).warning("scoreboard live enrichment failed: %s", exc)
        report["live_enrichment_error"] = str(exc)
    return report
