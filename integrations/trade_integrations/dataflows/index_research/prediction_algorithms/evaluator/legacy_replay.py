"""Replay legacy headline path (predict → scenario reconcile → finalize) for backtests."""

from __future__ import annotations

from typing import Any

from trade_integrations.dataflows.index_research.models import ConstituentSignal
from trade_integrations.dataflows.index_research.predictor import (
    finalize_index_prediction,
    predict_nifty,
)
from trade_integrations.dataflows.index_research.scenarios import reconcile_prediction_with_scenarios


def replay_legacy_headline(
    *,
    spot: float,
    signals: list[ConstituentSignal],
    macro_factors: dict[str, Any],
    scenarios: list[dict[str, Any]],
    scenario_anchor: float | None,
    horizon,
    model_artifact,
    as_of_day: str,
    macro_trust_multiplier: float = 1.0,
) -> dict[str, Any] | None:
    """Approximate live headline (no debate merge) at a historical anchor date."""
    if spot <= 0 or model_artifact is None:
        return None
    pred = predict_nifty(
        spot=spot,
        signals=signals,
        macro_factors=macro_factors,
        horizon=horizon,
        model_artifact=model_artifact,
        scenario_anchor_return_pct=scenario_anchor,
        as_of_day=as_of_day,
        macro_trust_multiplier=macro_trust_multiplier,
    )
    if not pred:
        return None
    mae_pct = float(getattr(model_artifact, "mae", None) or 1.5)
    if scenarios:
        pred = reconcile_prediction_with_scenarios(
            pred,
            scenarios,
            spot=spot,
            mae_pct=mae_pct,
        )
    return finalize_index_prediction(
        pred,
        spot=spot,
        mae_pct=mae_pct,
        macro_factors=macro_factors,
        scenario_anchor_return_pct=scenario_anchor,
    ) | {
        "debate_merged": False,
        "reconciled_with_scenarios": bool(scenarios and pred.get("reconciled_with_scenarios")),
    }
