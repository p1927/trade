"""Single entry point for the forecast lab."""

from __future__ import annotations

from typing import Literal

from trade_integrations.dataflows.index_research.prediction_algorithms.causes.cause_stress_index import (
    compute_cause_stress_index,
)
from trade_integrations.dataflows.index_research.prediction_algorithms.causes.channel_attribution import (
    compute_channel_attribution,
)
from trade_integrations.dataflows.index_research.prediction_algorithms.combiners import run_combiner
from trade_integrations.dataflows.index_research.prediction_algorithms.config import default_combiner_id
from trade_integrations.dataflows.index_research.prediction_algorithms.promotion import resolve_active_combiner
from trade_integrations.dataflows.index_research.prediction_algorithms.registry import run_all_tracks
from trade_integrations.dataflows.index_research.prediction_algorithms.types import ForecastLabResult, TrackContext

LabRunMode = Literal["tracks_only", "combine"]


def run_forecast_lab(
    context: TrackContext,
    *,
    mode: LabRunMode = "tracks_only",
    combiner_id: str | None = None,
    include_causes: bool = True,
    mae_by_track: dict[str, float] | None = None,
) -> ForecastLabResult:
    """Run all forecast tracks and optionally combine."""
    tracks = run_all_tracks(context)
    track_dict = {tid: row.to_dict() for tid, row in tracks.items()}

    cause_meta = compute_cause_stress_index(context.macro_factors) if include_causes else {}
    channel = None
    if include_causes:
        coefs = None
        if context.model_artifact:
            coefs = context.model_artifact.coefficients
        channel = compute_channel_attribution(context.macro_factors, coefficients=coefs)

    combiner_result = None
    active = combiner_id or resolve_active_combiner(default=default_combiner_id())
    if mode == "combine":
        combined = run_combiner(
            active,
            tracks,
            cause_stress_index=cause_meta.get("cause_stress_index"),
            mae_by_track=mae_by_track,
        )
        combiner_result = combined.to_dict()

    return ForecastLabResult(
        ticker=context.ticker,
        horizon_days=context.horizon.days,
        mode=mode,
        enabled=True,
        forecast_tracks=track_dict,
        combiner=combiner_result,
        cause_stress_index=cause_meta.get("cause_stress_index"),
        cause_stress_label=cause_meta.get("cause_stress_label"),
        active_causes=cause_meta.get("active_causes") or [],
        channel_attribution=channel,
        active_combiner=active if mode == "combine" else None,
        meta={"track_count": len(track_dict), "recommended_refresh": cause_meta.get("recommended_refresh")},
    )
