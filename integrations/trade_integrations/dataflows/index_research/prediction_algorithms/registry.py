"""Track and combiner registry."""

from __future__ import annotations

import contextvars
import logging
import os
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError, as_completed
from contextlib import nullcontext
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
from trade_integrations.dataflows.index_research.prediction_algorithms.tracks.reconciled_hybrid import (
    run_reconciled_hybrid,
)
from trade_integrations.dataflows.index_research.prediction_algorithms.tracks.scenario_anchor import run_scenario_anchor
from trade_integrations.dataflows.index_research.prediction_algorithms.tracks.xgboost_macro import run_xgboost_macro
from trade_integrations.dataflows.index_research.pipeline_log import PipelineLogger
from trade_integrations.dataflows.index_research.prediction_algorithms.types import ForecastTrack, TrackContext

logger = logging.getLogger(__name__)

TrackRunner = Callable[[TrackContext], ForecastTrack]

_TRACK_POOL_WORKERS = max(1, int(os.getenv("INDEX_PREDICTION_TRACK_POOL_WORKERS", "2")))
_TRACK_TIMEOUT_S = float(os.getenv("INDEX_PREDICTION_TRACK_TIMEOUT_S", "45"))

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
    "reconciled_hybrid": run_reconciled_hybrid,
}

EXPERIMENTAL_TRACK_REGISTRY: dict[str, TrackRunner] = {
    "lightgbm_macro": run_lightgbm_macro,
    "xgboost_macro": run_xgboost_macro,
    "arimax_macro": run_arimax_macro,
    "darts_macro": run_darts_macro,
    "automl_cached": run_automl_cached,
}


def _run_track_timed(
    track_id: str,
    runner: TrackRunner,
    ctx: TrackContext,
    *,
    pipeline: PipelineLogger | None,
) -> ForecastTrack:
    from trade_integrations.dataflows.index_research.prediction_algorithms.track_constants import (
        TRACK_BACKTEST_ELIGIBLE,
    )
    from trade_integrations.dataflows.index_research.stage_budget import check_stage_budget

    check_stage_budget("forecast_lab", token=f"forecast_lab:{track_id}")
    with (pipeline.stage_timer(
        "forecast_lab",
        f"Track {track_id}",
        track_id=track_id,
        budget_token=f"forecast_lab:{track_id}",
    ) if pipeline else nullcontext()):
        if pipeline is not None:
            pipeline.info("forecast_lab", f"Running track: {track_id}…", track_id=track_id)
        single = ThreadPoolExecutor(max_workers=1, thread_name_prefix=f"track-{track_id}")
        try:
            fut = single.submit(safe_run_track, track_id, runner, ctx)
            track = fut.result(timeout=_TRACK_TIMEOUT_S)
        except FuturesTimeoutError:
            eligible = TRACK_BACKTEST_ELIGIBLE.get(track_id, False)
            track = ForecastTrack(
                track_id=track_id,
                expected_return_pct=0.0,
                view="neutral",
                available=False,
                backtest_eligible=eligible,
                provenance={"error": "timeout", "reason": f"exceeded {_TRACK_TIMEOUT_S}s"},
            )
        finally:
            single.shutdown(wait=False, cancel_futures=True)
    if pipeline is not None:
        if track.available:
            pipeline.info(
                "forecast_lab",
                f"Track {track_id}: {track.view} {track.expected_return_pct:+.2f}%",
                track_id=track_id,
                expected_return_pct=track.expected_return_pct,
            )
        else:
            reason = (track.provenance or {}).get("reason") or (track.provenance or {}).get("error") or "unavailable"
            pipeline.info(
                "forecast_lab",
                f"Track {track_id}: skipped ({reason})",
                track_id=track_id,
                reason=reason,
            )
    return track


def run_all_tracks(
    ctx: TrackContext,
    track_ids: list[str] | None = None,
    *,
    pipeline: PipelineLogger | None = None,
) -> dict[str, ForecastTrack]:
    ids = track_ids or list(TRACK_REGISTRY.keys())
    if track_ids is None and experimental_tracks_enabled():
        ids = list(ids) + [tid for tid in EXPERIMENTAL_TRACK_IDS if tid in EXPERIMENTAL_TRACK_REGISTRY]
    out: dict[str, ForecastTrack] = {}
    runnable: list[tuple[str, TrackRunner]] = []
    for track_id in ids:
        runner = TRACK_REGISTRY.get(track_id) or EXPERIMENTAL_TRACK_REGISTRY.get(track_id)
        if runner is None:
            logger.warning("Unknown forecast track id: %s", track_id)
            continue
        runnable.append((track_id, runner))

    if not runnable:
        return out

    if len(runnable) == 1 or _TRACK_POOL_WORKERS <= 1:
        for track_id, runner in runnable:
            out[track_id] = _run_track_timed(track_id, runner, ctx, pipeline=pipeline)
        return out

    def _submit_track(track_id: str, runner: TrackRunner):
        # Fresh context per worker — one shared Context cannot be entered concurrently.
        return pool.submit(
            contextvars.copy_context().run,
            _run_track_timed,
            track_id,
            runner,
            ctx,
            pipeline=pipeline,
        )

    with ThreadPoolExecutor(max_workers=_TRACK_POOL_WORKERS, thread_name_prefix="forecast-tracks") as pool:
        futures = {_submit_track(track_id, runner): track_id for track_id, runner in runnable}
        for fut in as_completed(futures):
            track_id = futures[fut]
            try:
                out[track_id] = fut.result()
            except Exception as exc:
                logger.warning("forecast track %s failed: %s", track_id, exc)
                from trade_integrations.dataflows.index_research.prediction_algorithms.track_constants import (
                    TRACK_BACKTEST_ELIGIBLE,
                )

                eligible = TRACK_BACKTEST_ELIGIBLE.get(track_id, False)
                out[track_id] = ForecastTrack(
                    track_id=track_id,
                    expected_return_pct=0.0,
                    view="neutral",
                    available=False,
                    backtest_eligible=eligible,
                    provenance={"error": str(exc)},
                )
    return out
