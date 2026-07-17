"""bottom_up track — constituent rollup (diagnostic only)."""

from __future__ import annotations

from trade_integrations.dataflows.index_research.attribution import attribute_constituents, rollup_attribution
from trade_integrations.dataflows.index_research.constituent_backtest import MIN_HYBRID_CONSTITUENTS
from trade_integrations.dataflows.index_research.prediction_algorithms.types import ForecastTrack, TrackContext
from trade_integrations.dataflows.index_research.views import classify_index_view


def run_bottom_up(ctx: TrackContext) -> ForecastTrack:
    signals = ctx.signals or []
    is_proxy = len(signals) == 1 and signals[0].symbol == "_INDEX_SENTIMENT"
    signal_count = len(signals)
    if not is_proxy and signal_count < MIN_HYBRID_CONSTITUENTS:
        return ForecastTrack(
            track_id="bottom_up",
            expected_return_pct=0.0,
            view="neutral",
            available=False,
            provenance={
                "reason": "insufficient_constituent_signals",
                "signal_count": signal_count,
                "min_hybrid_constituents": MIN_HYBRID_CONSTITUENTS,
            },
        )

    attributed = attribute_constituents(signals, horizon_days=ctx.horizon.days)
    rollup = rollup_attribution(attributed)
    value = float(rollup.get("total_contribution_pct") or 0.0)
    return ForecastTrack(
        track_id="bottom_up",
        expected_return_pct=round(value, 4),
        view=classify_index_view(value),
        provenance={
            "signal_count": signal_count,
            "min_hybrid_constituents": MIN_HYBRID_CONSTITUENTS,
            "proxy": is_proxy,
            "top_drivers": (rollup.get("top_drivers") or [])[:3],
        },
    )
