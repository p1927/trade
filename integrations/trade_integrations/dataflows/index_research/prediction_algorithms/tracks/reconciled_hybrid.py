"""Reconciled hybrid track — MinTrace merge of bottom-up + macro."""

from __future__ import annotations

from trade_integrations.dataflows.index_research.prediction_algorithms.reconciliation.min_trace import (
    reconcile_hybrid_forecast,
)
from trade_integrations.dataflows.index_research.prediction_algorithms.tracks.bottom_up import run_bottom_up
from trade_integrations.dataflows.index_research.prediction_algorithms.tracks.macro_only_no_overlay import (
    run_macro_only_no_overlay,
)
from trade_integrations.dataflows.index_research.prediction_algorithms.types import ForecastTrack, TrackContext
from trade_integrations.dataflows.index_research.views import classify_index_view


def run_reconciled_hybrid(ctx: TrackContext) -> ForecastTrack:
    bottom_up_track = run_bottom_up(ctx)
    macro_track = run_macro_only_no_overlay(ctx)
    if not bottom_up_track.available or not macro_track.available:
        return ForecastTrack(
            track_id="reconciled_hybrid",
            expected_return_pct=0.0,
            view="neutral",
            available=False,
            provenance={
                "reason": "bottom_up or macro_only_no_overlay unavailable",
                "bottom_up_available": bottom_up_track.available,
                "macro_available": macro_track.available,
            },
        )

    result = reconcile_hybrid_forecast(
        bottom_up_track.expected_return_pct,
        macro_track.expected_return_pct,
    )
    value = float(result["expected_return_pct"])
    return ForecastTrack(
        track_id="reconciled_hybrid",
        expected_return_pct=value,
        view=classify_index_view(value),
        provenance={
            "reconciliation_weights": result["reconciliation_weights"],
            "bottom_up_return_pct": result["bottom_up_return_pct"],
            "macro_only_return_pct": result["macro_only_return_pct"],
        },
    )
