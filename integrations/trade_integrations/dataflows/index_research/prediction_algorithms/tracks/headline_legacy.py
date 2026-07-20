"""headline_legacy track — post-reconcile headline (+ debate when merged live)."""

from __future__ import annotations

from trade_integrations.dataflows.index_research.prediction_algorithms.types import ForecastTrack, TrackContext


def run_headline_legacy(ctx: TrackContext) -> ForecastTrack:
    legacy = ctx.legacy_prediction or {}
    if not legacy or legacy.get("expected_return_pct") is None:
        return ForecastTrack(
            track_id="headline_legacy",
            expected_return_pct=0.0,
            view="neutral",
            available=False,
            provenance={"reason": "legacy_prediction_unavailable"},
        )

    return ForecastTrack(
        track_id="headline_legacy",
        expected_return_pct=float(legacy.get("expected_return_pct") or 0.0),
        view=str(legacy.get("view") or "neutral"),
        provenance={
            "reconciled_with_scenarios": legacy.get("reconciled_with_scenarios"),
            "debate_merged": legacy.get("debate_merged"),
        },
    )
