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
    ML_SEQUENTIAL_TRACK_IDS,
    ML_TABULAR_TRACK_IDS,
    debate_backtest_eligible,
)

_MIN_EVAL_COUNT = 60
_VIEW_MARGIN = 0.03
_WEIGHT_STABILITY_MAX_STD = 0.25
_CONSECUTIVE_RUNS_REQUIRED = 2
_BOOTSTRAP_SAMPLES = 500
_BOOTSTRAP_CONFIDENCE = 0.95
_BOOTSTRAP_MIN_PAIRS = 30
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


def _paired_view_hits(
    daily_evaluations: list[dict[str, Any]],
    combiner_id: str,
    *,
    quant_track_id: str = "quant_ridge",
) -> list[tuple[bool, bool]]:
    """Align combiner vs quant view_hit booleans by evaluation date."""
    combiner_track = f"combiner:{combiner_id}"
    by_date: dict[str, dict[str, bool]] = {}
    for row in daily_evaluations:
        tid = str(row.get("track_id") or "")
        if tid not in (combiner_track, quant_track_id):
            continue
        day = str(row.get("date") or "")[:10]
        if not day:
            continue
        hit = row.get("view_hit")
        if hit is None:
            continue
        by_date.setdefault(day, {})[tid] = bool(hit)
    pairs: list[tuple[bool, bool]] = []
    for hits in by_date.values():
        if combiner_track in hits and quant_track_id in hits:
            pairs.append((hits[combiner_track], hits[quant_track_id]))
    return pairs


def bootstrap_view_margin_ci(
    daily_evaluations: list[dict[str, Any]],
    combiner_id: str,
    *,
    quant_track_id: str = "quant_ridge",
    n_samples: int = _BOOTSTRAP_SAMPLES,
    confidence: float = _BOOTSTRAP_CONFIDENCE,
    margin: float = _VIEW_MARGIN,
) -> dict[str, Any]:
    """Bootstrap lower bound on combiner view-hit rate minus quant (premortem M1)."""
    pairs = _paired_view_hits(daily_evaluations, combiner_id, quant_track_id=quant_track_id)
    n_pairs = len(pairs)
    if n_pairs < _BOOTSTRAP_MIN_PAIRS:
        return {
            "passes": False,
            "reason": "insufficient_pairs",
            "n_pairs": n_pairs,
            "min_pairs": _BOOTSTRAP_MIN_PAIRS,
        }

    import random

    rng = random.Random(42)
    deltas: list[float] = []
    for _ in range(n_samples):
        sample = [pairs[rng.randrange(n_pairs)] for _ in range(n_pairs)]
        comb_rate = sum(1 for c, _q in sample if c) / n_pairs
        quant_rate = sum(1 for _c, q in sample if q) / n_pairs
        deltas.append(comb_rate - quant_rate)
    deltas.sort()
    lower_idx = max(0, int((1.0 - confidence) / 2.0 * n_samples))
    lower = deltas[lower_idx]
    return {
        "passes": lower >= margin,
        "lower_bound_pp": round(lower * 100.0, 2),
        "margin_pp": round(margin * 100.0, 2),
        "n_pairs": n_pairs,
        "confidence": confidence,
        "n_samples": n_samples,
    }


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


_MAE_IMPROVEMENT_RATIO = 0.98


def evaluate_promotion(scoreboard: dict[str, Any], *, ticker: str = "NIFTY") -> dict[str, Any]:
    """Return promotion verdict for each combiner vs quant_ridge (view + MAE)."""
    tracks = scoreboard.get("tracks") or {}
    combiners = scoreboard.get("combiners") or {}
    quant = tracks.get("quant_ridge") or {}
    if quant.get("backtest_eligible") is False:
        quant = {}
    quant_view = float(quant.get("view_hit_rate") or quant.get("direction_hit_rate") or 0.0)
    quant_mae = float(quant.get("mae_pct") or 999.0)
    eval_count = int(scoreboard.get("eval_count") or quant.get("eval_count") or 0)
    debate_archive_ok = debate_backtest_eligible(ticker)

    verdicts: dict[str, Any] = {}
    equal = combiners.get("equal_weight_2") or {}
    equal_view = float(equal.get("view_hit_rate") or equal.get("direction_hit_rate") or 0.0)

    raw_promoted: list[str] = []
    for cid, row in combiners.items():
        if cid == "quant_only":
            continue
        hit = float(row.get("view_hit_rate") or row.get("direction_hit_rate") or 0.0)
        dir_hit = float(row.get("direction_hit_rate") or 0.0)
        row_mae = float(row.get("mae_pct") or 999.0)
        mae_passes = row_mae <= quant_mae * _MAE_IMPROVEMENT_RATIO if quant_mae < 900 else True
        passes = (
            eval_count >= _MIN_EVAL_COUNT
            and hit >= quant_view + _VIEW_MARGIN
            and hit >= equal_view
            and mae_passes
        )
        verdicts[cid] = {
            "promoted": passes,
            "view_hit_rate": hit,
            "direction_hit_rate": dir_hit,
            "mae_pct": row_mae,
            "quant_mae_pct": quant_mae if quant_mae < 900 else None,
            "mae_passes": mae_passes,
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
    daily = scoreboard.get("daily_evaluations") or []
    bootstrap_by_combiner: dict[str, Any] = {}
    headline_bootstrap: dict[str, Any] = {}
    if daily:
        quant_rows = [
            {
                "direction_correct": r.get("direction_hit"),
            }
            for r in daily
            if r.get("track_id") == "quant_ridge" and r.get("direction_hit") is not None
        ]
        if quant_rows:
            from trade_integrations.dataflows.index_research.walk_forward_utils import bootstrap_direction_ci

            headline_bootstrap = bootstrap_direction_ci(
                [{"direction_correct": bool(r["direction_correct"])} for r in quant_rows]
            )
    if not daily:
        stable_bootstrap = stable
    else:
        bootstrap_ok: list[str] = []
        for cid in raw_promoted:
            ci = bootstrap_view_margin_ci(daily, cid)
            bootstrap_by_combiner[cid] = ci
            if ci.get("passes"):
                bootstrap_ok.append(cid)
        stable_bootstrap = [c for c in stable if c in bootstrap_ok]

    return {
        "eval_count": eval_count,
        "quant_view_hit_rate": quant_view,
        "quant_direction_hit_rate": float(quant.get("direction_hit_rate") or 0.0),
        "quant_mae_pct": quant_mae if quant_mae < 900 else None,
        "mae_improvement_ratio": _MAE_IMPROVEMENT_RATIO,
        "verdicts": verdicts,
        "promoted_combiners": stable_bootstrap,
        "raw_promoted_combiners": raw_promoted,
        "auto_promote_allowed": bool(stable_bootstrap)
        and eval_count >= _MIN_EVAL_COUNT
        and not bool(headline_bootstrap.get("insufficient_evidence")),
        "min_eval_count_required": _MIN_EVAL_COUNT,
        "consecutive_runs_required": _CONSECUTIVE_RUNS_REQUIRED,
        "bootstrap_ci": bootstrap_by_combiner,
        "headline_direction_bootstrap_ci": headline_bootstrap,
        "headline_auto_promote_blocked": bool(headline_bootstrap.get("insufficient_evidence")),
        "bootstrap_min_pairs": _BOOTSTRAP_MIN_PAIRS,
        "debate_archive_eligible": debate_archive_ok,
        "debate_numeric_promotion_blocked": not debate_archive_ok,
    }


def finalize_scoreboard_promotion(report: dict[str, Any], *, ticker: str = "NIFTY") -> dict[str, Any]:
    """Attach promotion verdict + run history before persisting scoreboard."""
    out = dict(report)
    prev = load_scoreboard(ticker)
    if prev and prev.get("promotion_run_history"):
        out["promotion_run_history"] = list(prev["promotion_run_history"])

    interim = evaluate_promotion(out, ticker=ticker)
    raw = interim.get("raw_promoted_combiners") or []
    out["promotion_run_history"] = _append_promotion_run_history(out, raw)
    promo = evaluate_promotion(out, ticker=ticker)
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
    if window is not None or combiner_id in ("shrinkage_50", "stacked_ridge_meta", "equal_weight_ml_3"):
        w = window or INVERSE_MAE_WINDOWS.get("inverse_mae_w6", 6)
        track_ids = list(COMBINER_THREE_TRACK_IDS)
        if combiner_id in ("stacked_ridge_meta", "equal_weight_ml_3"):
            track_ids = ["quant_ridge", *ML_TABULAR_TRACK_IDS, *ML_SEQUENTIAL_TRACK_IDS]
        kwargs["mae_by_track"] = mae_by_track_from_scoreboard(
            board,
            track_ids=track_ids,
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

    promo = evaluate_promotion(board, ticker=ticker)
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
