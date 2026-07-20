"""event_overlay track — calibrated news shock overlay."""

from __future__ import annotations

from trade_integrations.dataflows.index_research.event_overlay import compute_event_overlay
from trade_integrations.dataflows.index_research.prediction_algorithms.types import ForecastTrack, TrackContext
from trade_integrations.dataflows.index_research.views import classify_index_view


def run_event_overlay(ctx: TrackContext) -> ForecastTrack:
    overlay = compute_event_overlay(
        ctx.macro_factors,
        as_of_day=ctx.as_of_day,
        horizon_days=ctx.horizon.days,
        ticker=ctx.ticker,
    )
    value = float(overlay.get("return_pct") or 0.0)
    method = str(overlay.get("method") or "unknown")
    return ForecastTrack(
        track_id="event_overlay",
        expected_return_pct=round(value, 4),
        view=classify_index_view(value),
        available=True,
        provenance={
            "method": method,
            "active_topics": overlay.get("active_topics") or [],
            "calibration_as_of": overlay.get("calibration_as_of"),
            "calibrated": method == "calibrated_ledger_v1",
        },
    )
