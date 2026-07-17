"""Combiner promotion gates (+3 pp direction vs quant)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from trade_integrations.context.hub import get_hub_dir
from trade_integrations.dataflows.index_research.prediction_algorithms.config import default_combiner_id

_MIN_EVAL_COUNT = 60
_DIRECTION_MARGIN = 0.03


def scoreboard_path(ticker: str = "NIFTY") -> Path:
    return get_hub_dir() / ticker.strip().upper() / "index_research" / "track_scoreboard_latest.json"


def load_scoreboard(ticker: str = "NIFTY") -> dict[str, Any] | None:
    path = scoreboard_path(ticker)
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def evaluate_promotion(scoreboard: dict[str, Any]) -> dict[str, Any]:
    """Return promotion verdict for each combiner vs quant_ridge."""
    tracks = scoreboard.get("tracks") or {}
    combiners = scoreboard.get("combiners") or {}
    quant = tracks.get("quant_ridge") or {}
    if quant.get("backtest_eligible") is False:
        quant = {}
    quant_dir = float(quant.get("direction_hit_rate") or 0.0)
    eval_count = int(scoreboard.get("eval_count") or quant.get("eval_count") or 0)

    verdicts: dict[str, Any] = {}
    equal = combiners.get("equal_weight_2") or {}
    equal_dir = float(equal.get("direction_hit_rate") or 0.0)

    for cid, row in combiners.items():
        if cid == "quant_only":
            continue
        hit = float(row.get("direction_hit_rate") or 0.0)
        passes = (
            eval_count >= _MIN_EVAL_COUNT
            and hit >= quant_dir + _DIRECTION_MARGIN
            and hit >= equal_dir
        )
        verdicts[cid] = {
            "promoted": passes,
            "direction_hit_rate": hit,
            "delta_vs_quant_pp": round((hit - quant_dir) * 100, 2),
            "eval_count": eval_count,
            "insufficient_evidence": eval_count < _MIN_EVAL_COUNT,
        }

    promoted = [cid for cid, v in verdicts.items() if v.get("promoted")]
    return {
        "eval_count": eval_count,
        "quant_direction_hit_rate": quant_dir,
        "verdicts": verdicts,
        "promoted_combiners": promoted,
        "auto_promote_allowed": eval_count >= _MIN_EVAL_COUNT,
        "min_eval_count_required": _MIN_EVAL_COUNT,
    }


def resolve_active_combiner(*, default: str | None = None, ticker: str = "NIFTY") -> str:
    """Pick combiner: env default, or auto from scoreboard if env is 'auto'."""
    env = (default or default_combiner_id()).strip()
    if env != "auto":
        return env or "quant_only"

    board = load_scoreboard(ticker)
    if not board:
        return "quant_only"

    promo = evaluate_promotion(board)
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
