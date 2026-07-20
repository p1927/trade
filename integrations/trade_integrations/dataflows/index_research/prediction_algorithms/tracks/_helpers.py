"""Track runner helpers."""

from __future__ import annotations

from typing import Callable

from trade_integrations.dataflows.index_research.prediction_algorithms.track_constants import (
    TRACK_BACKTEST_ELIGIBLE,
)
from trade_integrations.dataflows.index_research.prediction_algorithms.types import (
    ForecastTrack,
    TrackContext,
)


def safe_run_track(
    track_id: str,
    runner: Callable[[TrackContext], ForecastTrack],
    ctx: TrackContext,
) -> ForecastTrack:
    eligible = TRACK_BACKTEST_ELIGIBLE.get(track_id, False)
    try:
        track = runner(ctx)
        if track_id == "debate_numeric":
            prov = track.provenance or {}
            if prov.get("backtest_eligible") is not None:
                track.backtest_eligible = bool(prov.get("backtest_eligible"))
            else:
                track.backtest_eligible = eligible
        else:
            track.backtest_eligible = eligible
        return track
    except Exception as exc:
        return ForecastTrack(
            track_id=track_id,
            expected_return_pct=0.0,
            view="neutral",
            available=False,
            backtest_eligible=eligible,
            provenance={"error": str(exc)},
        )
