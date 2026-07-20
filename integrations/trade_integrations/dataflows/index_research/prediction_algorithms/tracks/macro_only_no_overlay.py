"""macro_only_no_overlay — macro delta without news overlay (for combiners with event_overlay track)."""

from __future__ import annotations

from trade_integrations.dataflows.index_research.macro_forecast import compute_macro_only_return
from trade_integrations.dataflows.index_research.predictor import load_stored_model_artifact
from trade_integrations.dataflows.index_research.prediction_algorithms.types import ForecastTrack, TrackContext
from trade_integrations.dataflows.index_research.views import classify_index_view


def run_macro_only_no_overlay(ctx: TrackContext) -> ForecastTrack:
    artifact = ctx.model_artifact or load_stored_model_artifact()
    if artifact is None or not artifact.feature_names:
        return ForecastTrack(
            track_id="macro_only_no_overlay",
            expected_return_pct=0.0,
            view="neutral",
            available=False,
            backtest_eligible=True,
            provenance={"reason": "no_model_artifact"},
        )

    macro, prov = compute_macro_only_return(
        ctx.macro_factors,
        ctx.horizon,
        artifact,
        scenario_anchor=ctx.scenario_anchor,
        as_of_day=ctx.as_of_day,
        macro_trust_multiplier=ctx.macro_trust_multiplier,
        ticker=ctx.ticker,
        include_event_overlay=False,
    )
    return ForecastTrack(
        track_id="macro_only_no_overlay",
        expected_return_pct=macro,
        view=classify_index_view(macro),
        backtest_eligible=True,
        provenance={**prov, "include_event_overlay": False},
    )
