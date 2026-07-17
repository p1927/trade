"""quant_ridge_no_overlay — hybrid quant without news/event overlay in macro (Phase G split)."""

from __future__ import annotations

from trade_integrations.dataflows.index_research.predictor import load_stored_model_artifact, predict_nifty
from trade_integrations.dataflows.index_research.prediction_algorithms.types import ForecastTrack, TrackContext


def run_quant_ridge_no_overlay(ctx: TrackContext) -> ForecastTrack:
    if ctx.spot <= 0:
        return ForecastTrack(
            track_id="quant_ridge_no_overlay",
            expected_return_pct=0.0,
            view="neutral",
            available=False,
            provenance={"reason": "spot_unavailable"},
        )

    artifact = ctx.model_artifact or load_stored_model_artifact()
    pred = predict_nifty(
        spot=ctx.spot,
        signals=ctx.signals,
        macro_factors=ctx.macro_factors,
        horizon=ctx.horizon,
        model_artifact=artifact,
        scenario_anchor_return_pct=ctx.scenario_anchor,
        as_of_day=ctx.as_of_day,
        macro_trust_multiplier=ctx.macro_trust_multiplier,
        apply_event_overlay=False,
    )
    return ForecastTrack(
        track_id="quant_ridge_no_overlay",
        expected_return_pct=float(pred.get("expected_return_pct") or 0.0),
        view=str(pred.get("view") or "neutral"),
        confidence=_optional_float(pred.get("direction_confidence")),
        provenance={
            "source": "predict_nifty",
            "apply_event_overlay": False,
            "bottom_up_return_pct": pred.get("bottom_up_return_pct"),
            "macro_delta_pct": pred.get("macro_delta_pct"),
            "event_overlay_pct_skipped": pred.get("event_overlay_pct"),
        },
    )


def _optional_float(raw) -> float | None:
    if raw is None:
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None
