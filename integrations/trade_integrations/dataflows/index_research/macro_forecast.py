"""Shared macro-only forecast path (gated Ridge + overlay + shrink)."""

from __future__ import annotations

from typing import Any

from trade_integrations.dataflows.index_research.event_overlay import merge_overlay_into_macro
from trade_integrations.dataflows.index_research.horizon import HorizonProfile
from trade_integrations.dataflows.index_research.predictor import ModelArtifact, shrink_macro_delta
from trade_integrations.dataflows.index_research.regime_gates import predict_macro_delta_gated


def compute_macro_only_return(
    macro_factors: dict[str, Any],
    horizon: HorizonProfile,
    artifact: ModelArtifact,
    *,
    scenario_anchor: float | None = None,
    as_of_day: str | None = None,
    macro_trust_multiplier: float = 1.0,
    ticker: str = "NIFTY",
    include_event_overlay: bool = True,
) -> tuple[float, dict[str, Any]]:
    """Canonical macro delta: gated → optional overlay on raw → shrink toward scenario anchor."""
    raw_macro = predict_macro_delta_gated(
        macro_factors,
        horizon,
        artifact,
        macro_trust_multiplier=macro_trust_multiplier,
    )
    overlay: dict[str, Any] = {"return_pct": 0.0, "method": "skipped"}
    macro_input = raw_macro
    if include_event_overlay:
        macro_input, overlay = merge_overlay_into_macro(
            raw_macro,
            macro_factors,
            as_of_day=as_of_day,
            ticker=ticker,
        )
    macro = shrink_macro_delta(macro_input, scenario_anchor)
    provenance = {
        "raw_macro_delta_pct": round(raw_macro, 4),
        "event_overlay_pct": overlay.get("return_pct"),
        "event_overlay_method": overlay.get("method"),
        "include_event_overlay": include_event_overlay,
    }
    return round(macro, 4), provenance
