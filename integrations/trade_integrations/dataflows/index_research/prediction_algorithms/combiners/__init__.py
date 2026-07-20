"""Combiner implementations."""

from __future__ import annotations

from typing import Callable

from trade_integrations.dataflows.index_research.prediction_algorithms.combiners._math import (
    alignment_combine,
    available_tracks,
    classify_combined,
    equal_weight_combine,
    inverse_mae_combine,
    shrink_weights,
    weighted_forecast,
)
from trade_integrations.dataflows.index_research.prediction_algorithms.track_constants import (
    COMBINER_QUANT_THREE_TRACK_IDS,
    COMBINER_THREE_TRACK_IDS,
    COMBINER_TWO_TRACK_IDS,
    ML_SEQUENTIAL_TRACK_IDS,
    ML_TABULAR_TRACK_IDS,
)
from trade_integrations.dataflows.index_research.prediction_algorithms.types import (
    CombinationResult,
    ForecastTrack,
)

CombinerFn = Callable[..., CombinationResult]


def combine_quant_only(tracks: dict[str, ForecastTrack], **_kwargs) -> CombinationResult:
    row = tracks.get("quant_ridge")
    if row and row.available:
        return CombinationResult(
            combiner_id="quant_only",
            expected_return_pct=row.expected_return_pct,
            view=row.view,
            weights={"quant_ridge": 1.0},
            tracks_used=["quant_ridge"],
        )
    usable = [t for t in tracks.values() if t.available]
    if not usable:
        return CombinationResult("quant_only", 0.0, "neutral")
    row = usable[0]
    return CombinationResult(
        combiner_id="quant_only",
        expected_return_pct=row.expected_return_pct,
        view=row.view,
        weights={row.track_id: 1.0},
        tracks_used=[row.track_id],
        provenance={"fallback": True},
    )


def combine_equal_weight(tracks: dict[str, ForecastTrack], track_ids: list[str], combiner_id: str) -> CombinationResult:
    usable = available_tracks(tracks, track_ids)
    value, weights = equal_weight_combine(usable)
    provenance: dict[str, object] = {}
    if len(usable) != len(track_ids):
        provenance["requested_tracks"] = list(track_ids)
        provenance["available_count"] = len(usable)
    return CombinationResult(
        combiner_id=combiner_id,
        expected_return_pct=round(value, 4),
        view=classify_combined(value),
        weights=weights,
        tracks_used=[t.track_id for t in usable],
        provenance=provenance,
    )


def combine_inverse_mae(
    tracks: dict[str, ForecastTrack],
    track_ids: list[str],
    combiner_id: str,
    mae_by_track: dict[str, float] | None = None,
) -> CombinationResult:
    usable = available_tracks(tracks, track_ids)
    mae = mae_by_track or {t.track_id: 1.0 for t in usable}
    value, weights = inverse_mae_combine(usable, mae)
    return CombinationResult(
        combiner_id=combiner_id,
        expected_return_pct=round(value, 4),
        view=classify_combined(value),
        weights=weights,
        tracks_used=[t.track_id for t in usable],
    )


def combine_shrinkage(
    tracks: dict[str, ForecastTrack],
    track_ids: list[str],
    combiner_id: str,
    mae_by_track: dict[str, float] | None = None,
    lam: float = 0.5,
) -> CombinationResult:
    usable = available_tracks(tracks, track_ids)
    _, equal_w = equal_weight_combine(usable)
    _, opt_w = inverse_mae_combine(usable, mae_by_track or {t.track_id: 1.0 for t in usable})
    weights = shrink_weights(opt_w, equal_w, lam)
    value = weighted_forecast(usable, weights)
    return CombinationResult(
        combiner_id=combiner_id,
        expected_return_pct=round(value, 4),
        view=classify_combined(value),
        weights=weights,
        tracks_used=[t.track_id for t in usable],
        provenance={"lambda": lam},
    )


def combine_alignment(
    tracks: dict[str, ForecastTrack],
    combiner_id: str = "alignment_grid",
    lam: float = 0.5,
) -> CombinationResult:
    quant = tracks.get("quant_ridge")
    scenario = tracks.get("scenario_anchor")
    if not quant or not scenario or not (quant.available and scenario.available):
        return combine_quant_only(tracks)
    value = alignment_combine(quant.expected_return_pct, scenario.expected_return_pct, lam)
    return CombinationResult(
        combiner_id=combiner_id,
        expected_return_pct=round(value, 4),
        view=classify_combined(value),
        weights={"quant_ridge": lam, "scenario_anchor": 1.0 - lam},
        tracks_used=["quant_ridge", "scenario_anchor"],
        provenance={"lambda": lam},
    )


def combine_stress_conditional(
    tracks: dict[str, ForecastTrack],
    cause_stress_index: float | None = None,
) -> CombinationResult:
    stress = float(cause_stress_index or 0.0)
    if stress >= 60:
        return combine_equal_weight(
            tracks,
            list(COMBINER_THREE_TRACK_IDS),
            "stress_conditional",
        )
    return combine_quant_only(tracks)


def combine_fixed_legacy(tracks: dict[str, ForecastTrack], **_kwargs) -> CombinationResult:
    legacy = tracks.get("headline_legacy")
    if legacy and legacy.available:
        return CombinationResult(
            combiner_id="fixed_legacy",
            expected_return_pct=legacy.expected_return_pct,
            view=legacy.view,
            weights={"headline_legacy": 1.0},
            tracks_used=["headline_legacy"],
        )
    return combine_quant_only(tracks)


def _ml_track_pool() -> list[str]:
    return ["quant_ridge", *ML_TABULAR_TRACK_IDS, *ML_SEQUENTIAL_TRACK_IDS]


def combine_stacked_ridge_meta(
    tracks: dict[str, ForecastTrack],
    mae_by_track: dict[str, float] | None = None,
) -> CombinationResult:
    """Inverse-MAE weighted blend of quant_ridge + ML experiment tracks."""
    pool = _ml_track_pool()
    usable = available_tracks(tracks, pool)
    if len(usable) < 2:
        return combine_quant_only(tracks)
    mae = mae_by_track or {t.track_id: 1.0 for t in usable}
    value, weights = inverse_mae_combine(usable, mae)
    return CombinationResult(
        combiner_id="stacked_ridge_meta",
        expected_return_pct=round(value, 4),
        view=classify_combined(value),
        weights=weights,
        tracks_used=[t.track_id for t in usable],
        provenance={"method": "inverse_mae_meta"},
    )


def combine_equal_weight_ml_3(
    tracks: dict[str, ForecastTrack],
    mae_by_track: dict[str, float] | None = None,
) -> CombinationResult:
    """Equal weight among best 3 ML tracks by inverse MAE (+ quant if in top 3)."""
    pool = _ml_track_pool()
    usable = available_tracks(tracks, pool)
    if not usable:
        return combine_quant_only(tracks)
    mae = mae_by_track or {t.track_id: 1.0 for t in usable}
    ranked = sorted(usable, key=lambda t: float(mae.get(t.track_id, 1.0)))
    top = ranked[:3]
    value, weights = equal_weight_combine(top)
    return CombinationResult(
        combiner_id="equal_weight_ml_3",
        expected_return_pct=round(value, 4),
        view=classify_combined(value),
        weights=weights,
        tracks_used=[t.track_id for t in top],
        provenance={"method": "equal_weight_top3_ml"},
    )


COMBINER_REGISTRY: dict[str, CombinerFn] = {
    "quant_only": combine_quant_only,
    "equal_weight_2": lambda tracks, **kw: combine_equal_weight(
        tracks, list(COMBINER_TWO_TRACK_IDS), "equal_weight_2"
    ),
    "equal_weight_3": lambda tracks, **kw: combine_equal_weight(
        tracks, list(COMBINER_THREE_TRACK_IDS), "equal_weight_3"
    ),
    "equal_weight_quant_3": lambda tracks, **kw: combine_equal_weight(
        tracks, list(COMBINER_QUANT_THREE_TRACK_IDS), "equal_weight_quant_3"
    ),
    "inverse_mae_w6": lambda tracks, **kw: combine_inverse_mae(
        tracks, list(COMBINER_THREE_TRACK_IDS), "inverse_mae_w6", kw.get("mae_by_track")
    ),
    "inverse_mae_w12": lambda tracks, **kw: combine_inverse_mae(
        tracks, list(COMBINER_THREE_TRACK_IDS), "inverse_mae_w12", kw.get("mae_by_track")
    ),
    "shrinkage_50": lambda tracks, **kw: combine_shrinkage(
        tracks,
        list(COMBINER_THREE_TRACK_IDS),
        "shrinkage_50",
        kw.get("mae_by_track"),
        lam=0.5,
    ),
    "alignment_grid": lambda tracks, **kw: combine_alignment(tracks, lam=float(kw.get("lam") or 0.5)),
    "stress_conditional": lambda tracks, **kw: combine_stress_conditional(tracks, kw.get("cause_stress_index")),
    "fixed_legacy": combine_fixed_legacy,
    "stacked_ridge_meta": lambda tracks, **kw: combine_stacked_ridge_meta(tracks, kw.get("mae_by_track")),
    "equal_weight_ml_3": lambda tracks, **kw: combine_equal_weight_ml_3(tracks, kw.get("mae_by_track")),
}


INVALID_COMBINER_TRACK_SETS: frozenset[frozenset[str]] = frozenset(
    {
        frozenset({"quant_ridge", "bottom_up"}),
        frozenset({"quant_ridge", "event_overlay"}),
        frozenset({"macro_only", "event_overlay"}),
    }
)


def _validate_track_set(track_ids: list[str]) -> str | None:
    present = frozenset(track_ids)
    for invalid in INVALID_COMBINER_TRACK_SETS:
        if invalid.issubset(present):
            return f"invalid_track_set:{','.join(sorted(invalid))}"
    return None


def run_combiner(
    combiner_id: str,
    tracks: dict[str, ForecastTrack],
    **kwargs,
) -> CombinationResult:
    fn = COMBINER_REGISTRY.get(combiner_id) or combine_quant_only
    result = fn(tracks, **kwargs)
    invalid = _validate_track_set(result.tracks_used)
    if invalid:
        fallback = combine_quant_only(tracks)
        fallback.provenance = {
            **dict(fallback.provenance or {}),
            "validation_error": invalid,
            "invalid_combiner": combiner_id,
            "fallback": "quant_only",
        }
        return fallback
    return result
