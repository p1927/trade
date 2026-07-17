"""Track and combiner registry."""

from __future__ import annotations

from typing import Callable

from trade_integrations.dataflows.index_research.prediction_algorithms.tracks._helpers import safe_run_track
from trade_integrations.dataflows.index_research.prediction_algorithms.tracks.bottom_up import run_bottom_up
from trade_integrations.dataflows.index_research.prediction_algorithms.tracks.debate_numeric import run_debate_numeric
from trade_integrations.dataflows.index_research.prediction_algorithms.tracks.event_overlay import run_event_overlay
from trade_integrations.dataflows.index_research.prediction_algorithms.tracks.headline_legacy import run_headline_legacy
from trade_integrations.dataflows.index_research.prediction_algorithms.tracks.macro_only import run_macro_only
from trade_integrations.dataflows.index_research.prediction_algorithms.tracks.naive_baselines import (
    run_naive_momentum,
    run_naive_zero,
)
from trade_integrations.dataflows.index_research.prediction_algorithms.tracks.quant_ridge import run_quant_ridge
from trade_integrations.dataflows.index_research.prediction_algorithms.tracks.quant_ridge_no_overlay import (
    run_quant_ridge_no_overlay,
)
from trade_integrations.dataflows.index_research.prediction_algorithms.tracks.scenario_anchor import run_scenario_anchor
from trade_integrations.dataflows.index_research.prediction_algorithms.types import ForecastTrack, TrackContext

TrackRunner = Callable[[TrackContext], ForecastTrack]

TRACK_REGISTRY: dict[str, TrackRunner] = {
    "quant_ridge": run_quant_ridge,
    "quant_ridge_no_overlay": run_quant_ridge_no_overlay,
    "macro_only": run_macro_only,
    "bottom_up": run_bottom_up,
    "scenario_anchor": run_scenario_anchor,
    "event_overlay": run_event_overlay,
    "naive_zero": run_naive_zero,
    "naive_momentum": run_naive_momentum,
    "debate_numeric": run_debate_numeric,
    "headline_legacy": run_headline_legacy,
}


def run_all_tracks(ctx: TrackContext, track_ids: list[str] | None = None) -> dict[str, ForecastTrack]:
    ids = track_ids or list(TRACK_REGISTRY.keys())
    out: dict[str, ForecastTrack] = {}
    for track_id in ids:
        runner = TRACK_REGISTRY.get(track_id)
        if runner is None:
            continue
        out[track_id] = safe_run_track(track_id, runner, ctx)
    return out
