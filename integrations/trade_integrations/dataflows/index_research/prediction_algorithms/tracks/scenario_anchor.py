"""scenario_anchor track — event-table outside view."""

from __future__ import annotations

from trade_integrations.dataflows.index_research.prediction_algorithms.types import ForecastTrack, TrackContext
from trade_integrations.dataflows.index_research.scenarios import scenario_weighted_return_pct
from trade_integrations.dataflows.index_research.views import classify_index_view


def run_scenario_anchor(ctx: TrackContext) -> ForecastTrack:
    if ctx.scenario_anchor is not None:
        value = float(ctx.scenario_anchor)
    elif ctx.spot > 0 and ctx.scenarios:
        value = float(scenario_weighted_return_pct(ctx.scenarios, spot=ctx.spot))
    else:
        return ForecastTrack(
            track_id="scenario_anchor",
            expected_return_pct=0.0,
            view="neutral",
            available=False,
            provenance={"reason": "no_scenarios"},
        )

    return ForecastTrack(
        track_id="scenario_anchor",
        expected_return_pct=round(value, 4),
        view=classify_index_view(value),
        provenance={"scenario_count": len(ctx.scenarios)},
    )
