"""Naive baseline tracks."""

from __future__ import annotations

from trade_integrations.dataflows.index_research.prediction_algorithms.types import ForecastTrack, TrackContext
from trade_integrations.dataflows.index_research.views import classify_index_view


def run_naive_zero(_ctx: TrackContext) -> ForecastTrack:
    return ForecastTrack(
        track_id="naive_zero",
        expected_return_pct=0.0,
        view="neutral",
        provenance={"rule": "constant_zero"},
    )


def _momentum_factor_key(horizon_days: int) -> str:
    return "nifty_return_7d" if horizon_days <= 7 else "nifty_return_14d"


def run_naive_momentum(ctx: TrackContext) -> ForecastTrack:
    factors = ctx.macro_factors or {}
    primary = _momentum_factor_key(ctx.horizon.days)
    raw = factors.get(primary)
    fallback = None
    if raw is None:
        fallback = "nifty_return_14d" if primary == "nifty_return_7d" else "nifty_return_7d"
        raw = factors.get(fallback)
    if raw is None:
        return ForecastTrack(
            track_id="naive_momentum",
            expected_return_pct=0.0,
            view="neutral",
            available=False,
            provenance={"reason": "momentum_factor_missing", "primary_factor": primary},
        )
    value = float(raw)
    used = primary if factors.get(primary) is not None else fallback
    return ForecastTrack(
        track_id="naive_momentum",
        expected_return_pct=round(value, 4),
        view=classify_index_view(value),
        provenance={"factor": used, "horizon_days": ctx.horizon.days},
    )
