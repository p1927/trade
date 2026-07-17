"""Attach forecast lab tracks to a live index prediction (post-reconcile/debate)."""

from __future__ import annotations

import logging
from typing import Any

from trade_integrations.dataflows.index_research.prediction_algorithms.api import run_forecast_lab
from trade_integrations.dataflows.index_research.prediction_algorithms.config import lab_enabled, lab_mode
from trade_integrations.dataflows.index_research.prediction_algorithms.context_builder import (
    build_track_context,
)
from trade_integrations.dataflows.index_research.prediction_algorithms.promotion import (
    evaluate_promotion,
    load_scoreboard,
)

logger = logging.getLogger(__name__)


def snapshot_pre_reconcile_prediction(prediction: dict[str, Any]) -> dict[str, Any]:
    """Capture quant_ridge inputs before scenario reconcile / debate."""
    keys = (
        "expected_return_pct",
        "view",
        "direction_view",
        "direction_confidence",
        "bottom_up_return_pct",
        "macro_delta_pct",
        "event_overlay_delta_pct",
        "scenario_anchor_return_pct",
    )
    return {k: prediction[k] for k in keys if k in prediction}


def snapshot_legacy_prediction(prediction: dict[str, Any]) -> dict[str, Any]:
    """Headline after reconcile+finalize, before debate merge (backtest parity)."""
    return {
        "expected_return_pct": prediction.get("expected_return_pct"),
        "view": prediction.get("view"),
        "reconciled_with_scenarios": prediction.get("reconciled_with_scenarios"),
        "debate_merged": False,
    }


def attach_forecast_lab(
    prediction: dict[str, Any],
    *,
    ticker: str,
    spot: float,
    horizon_days: int,
    macro_factors: dict[str, Any],
    signals: list,
    scenarios: list[dict[str, Any]],
    scenario_anchor: float | None,
    as_of_day: str,
    macro_trust_multiplier: float = 1.0,
    debate_payload: dict[str, Any] | None = None,
    pre_reconcile_snapshot: dict[str, Any] | None = None,
    legacy_prediction: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Run forecast lab and merge track output into ``prediction``."""
    if not lab_enabled() or spot <= 0 or not prediction:
        return prediction

    pre = pre_reconcile_snapshot or snapshot_pre_reconcile_prediction(prediction)
    legacy = legacy_prediction or snapshot_legacy_prediction(prediction)

    prediction["forecast_lab_context"] = {
        "pre_reconcile_snapshot": pre,
        "legacy_prediction": legacy,
    }

    try:
        ctx = build_track_context(
            ticker=ticker,
            spot=spot,
            horizon_days=horizon_days,
            macro_factors=macro_factors,
            signals=signals,
            scenarios=scenarios,
            scenario_anchor=scenario_anchor,
            as_of_day=as_of_day,
            macro_trust_multiplier=macro_trust_multiplier,
            debate_payload=debate_payload,
            prediction_snapshot=pre,
            legacy_prediction=legacy,
        )
        run_mode = "combine" if lab_mode() == "combine" else "tracks_only"
        lab_result = run_forecast_lab(ctx, mode=run_mode)
        lab_dict = lab_result.to_dict()

        prediction["forecast_tracks"] = lab_dict.get("forecast_tracks") or {}
        if lab_dict.get("cause_stress_index") is not None:
            prediction["cause_stress_index"] = lab_dict.get("cause_stress_index")
            prediction["cause_stress_label"] = lab_dict.get("cause_stress_label")
            prediction["active_causes"] = lab_dict.get("active_causes") or []
        if lab_dict.get("channel_attribution"):
            prediction["channel_attribution"] = lab_dict.get("channel_attribution")

        combiner = lab_dict.get("combiner")
        active = lab_dict.get("active_combiner")
        if combiner:
            prediction["combiner_preview"] = combiner
        if active:
            prediction["active_combiner"] = active

        # Only override headline when combine mode + scoreboard promotion gates pass.
        if lab_mode() == "combine" and combiner and _promoted_combiner_active(ticker, str(active or "")):
            prediction["expected_return_pct"] = combiner.get("expected_return_pct")
            prediction["view"] = combiner.get("view")
            prediction["headline_source"] = f"combiner:{active}"
        elif lab_mode() == "combine" and combiner:
            prediction.setdefault("headline_source", "quant_pipeline")
    except Exception as exc:
        logger.debug("forecast lab skipped: %s", exc)

    return prediction


def _promoted_combiner_active(ticker: str, combiner_id: str) -> bool:
    if not combiner_id or combiner_id == "quant_only":
        return False
    board = load_scoreboard(ticker)
    if not board:
        return False
    promo = evaluate_promotion(board)
    if not promo.get("auto_promote_allowed"):
        return False
    promoted = promo.get("promoted_combiners") or []
    return combiner_id in promoted
