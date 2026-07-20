"""Track and combiner registry."""

from __future__ import annotations

import logging
from typing import Callable

from trade_integrations.dataflows.index_research.prediction_algorithms.config import experimental_tracks_enabled
from trade_integrations.dataflows.index_research.prediction_algorithms.track_constants import (
    EXPERIMENTAL_TRACK_IDS,
)
from trade_integrations.dataflows.index_research.prediction_algorithms.tracks._helpers import safe_run_track
from trade_integrations.dataflows.index_research.prediction_algorithms.tracks.arimax_macro import run_arimax_macro
from trade_integrations.dataflows.index_research.prediction_algorithms.tracks.automl_cached import run_automl_cached
from trade_integrations.dataflows.index_research.prediction_algorithms.tracks.bottom_up import run_bottom_up
from trade_integrations.dataflows.index_research.prediction_algorithms.tracks.darts_macro import run_darts_macro
from trade_integrations.dataflows.index_research.prediction_algorithms.tracks.debate_numeric import run_debate_numeric
from trade_integrations.dataflows.index_research.prediction_algorithms.tracks.event_overlay import run_event_overlay
from trade_integrations.dataflows.index_research.prediction_algorithms.tracks.headline_legacy import run_headline_legacy
from trade_integrations.dataflows.index_research.prediction_algorithms.tracks.lightgbm_macro import run_lightgbm_macro
from trade_integrations.dataflows.index_research.prediction_algorithms.tracks.macro_only import run_macro_only
from trade_integrations.dataflows.index_research.prediction_algorithms.tracks.macro_only_no_overlay import (
    run_macro_only_no_overlay,
)
from trade_integrations.dataflows.index_research.prediction_algorithms.tracks.naive_baselines import (
    run_naive_momentum,
    run_naive_zero,
)
from trade_integrations.dataflows.index_research.prediction_algorithms.tracks.quant_ridge import run_quant_ridge
from trade_integrations.dataflows.index_research.prediction_algorithms.tracks.quant_ridge_no_overlay import (
    run_quant_ridge_no_overlay,
)
from trade_integrations.dataflows.index_research.prediction_algorithms.tracks.scenario_anchor import run_scenario_anchor
from trade_integrations.dataflows.index_research.prediction_algorithms.tracks.xgboost_macro import run_xgboost_macro
from trade_integrations.dataflows.index_research.prediction_algorithms.types import ForecastTrack, TrackContext

logger = logging.getLogger(__name__)

TrackRunner = Callable[[TrackContext], ForecastTrack]

TRACK_REGISTRY: dict[str, TrackRunner] = {
    "quant_ridge": run_quant_ridge,
    "quant_ridge_no_overlay": run_quant_ridge_no_overlay,
    "macro_only": run_macro_only,
    "macro_only_no_overlay": run_macro_only_no_overlay,
    "bottom_up": run_bottom_up,
    "scenario_anchor": run_scenario_anchor,
    "event_overlay": run_event_overlay,
    "naive_zero": run_naive_zero,
    "naive_momentum": run_naive_momentum,
    "debate_numeric": run_debate_numeric,
    "headline_legacy": run_headline_legacy,
}

EXPERIMENTAL_TRACK_REGISTRY: dict[str, TrackRunner] = {
    "lightgbm_macro": run_lightgbm_macro,
    "xgboost_macro": run_xgboost_macro,
    "arimax_macro": run_arimax_macro,
    "darts_macro": run_darts_macro,
    "automl_cached": run_automl_cached,
}


def run_all_tracks(ctx: TrackContext, track_ids: list[str] | None = None) -> dict[str, ForecastTrack]:
    ids = track_ids or list(TRACK_REGISTRY.keys())
    if track_ids is None and experimental_tracks_enabled():
        ids = list(ids) + [tid for tid in EXPERIMENTAL_TRACK_IDS if tid in EXPERIMENTAL_TRACK_REGISTRY]
    out: dict[str, ForecastTrack] = {}
    for track_id in ids:
        runner = TRACK_REGISTRY.get(track_id) or EXPERIMENTAL_TRACK_REGISTRY.get(track_id)
        if runner is None:
            logger.warning("Unknown forecast track id: %s", track_id)
            continue
        out[track_id] = safe_run_track(track_id, runner, ctx)
    return out
