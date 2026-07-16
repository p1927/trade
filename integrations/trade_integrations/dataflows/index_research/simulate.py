"""What-if simulation — adjust macro factors and recompute index forecast."""

from __future__ import annotations

import copy
from typing import Any

from trade_integrations.dataflows.index_research.explain import (
    build_factor_sensitivity,
    explain_macro_factors,
)
from trade_integrations.dataflows.index_research.cascade.event_presets import (
    overrides_from_event_preset,
)
from trade_integrations.dataflows.index_research.cascade.engine import build_cascade_overrides
from trade_integrations.dataflows.index_research.cascade.regime_scaler import (
    classify_cascade_regime,
)
from trade_integrations.dataflows.index_research.cascade.rule_provider import build_rule_provider
from trade_integrations.dataflows.index_research.cascade.types import CascadeCalibration
from trade_integrations.dataflows.index_research.horizon import HorizonProfile, resolve_horizon
from trade_integrations.dataflows.index_research.predictor import (
    cap_macro_delta,
    load_stored_model_artifact,
    _predict_macro_delta,
)


def macro_factors_from_rows(global_factors: list[dict[str, Any]]) -> dict[str, Any]:
    """Rebuild macro factor dict from hub global_factors rows."""
    factors: dict[str, Any] = {}
    for row in global_factors:
        key = row.get("factor")
        if not key:
            continue
        value = row.get("value")
        if value is None:
            continue
        try:
            factors[str(key)] = float(value)
        except (TypeError, ValueError):
            continue
    return factors


def build_forecast_path(
    *,
    spot: float,
    baseline_return_pct: float,
    scenario_return_pct: float,
    horizon_days: int,
) -> list[dict[str, Any]]:
    """Linear spot → horizon paths for baseline and scenario (day 0 .. horizon)."""
    if spot <= 0 or horizon_days < 1:
        return []
    baseline_target = spot * (1.0 + baseline_return_pct / 100.0)
    scenario_target = spot * (1.0 + scenario_return_pct / 100.0)
    rows: list[dict[str, Any]] = []
    for day in range(horizon_days + 1):
        t = day / horizon_days
        rows.append(
            {
                "day": day,
                "baseline_level": round(spot + (baseline_target - spot) * t, 2),
                "scenario_level": round(spot + (scenario_target - spot) * t, 2),
                "baseline_return_pct": round(baseline_return_pct * t, 4),
                "scenario_return_pct": round(scenario_return_pct * t, 4),
            }
        )
    return rows


def resolve_factor_overrides(
    macro_factors: dict[str, Any],
    *,
    factor_overrides: dict[str, float] | None = None,
    primary_factor: str | None = None,
    primary_shock_pct: float | None = None,
    cascade: bool = True,
    event_preset_id: str | None = None,
    event_impact_curves: list[dict[str, Any]] | None = None,
    cascade_calibration: CascadeCalibration | None = None,
    india_vix: float | None = None,
    force_heuristic_cascade: bool = False,
) -> tuple[dict[str, float], list[dict[str, Any]]]:
    """Merge explicit overrides with primary shock / event preset cascade."""
    applied: list[dict[str, Any]] = []

    if event_preset_id and event_impact_curves:
        overrides, applied = overrides_from_event_preset(
            event_impact_curves,
            event_preset_id,
            macro_factors,
        )
        if overrides:
            return overrides, applied

    if primary_factor and primary_shock_pct is not None:
        regime = classify_cascade_regime(india_vix=india_vix)
        provider = build_rule_provider(
            cascade_calibration,
            regime=regime,
            force_heuristic=force_heuristic_cascade,
        )
        overrides, applied = build_cascade_overrides(
            primary_factor,
            float(primary_shock_pct),
            macro_factors,
            cascade=cascade,
            rule_provider=provider,
        )
        if factor_overrides:
            for key, value in factor_overrides.items():
                before = float(macro_factors.get(key, 0.0) or 0.0)
                overrides[key] = value
                applied.append(
                    {
                        "factor": key,
                        "before": round(before, 4),
                        "after": round(value, 4),
                        "reason": "explicit_override",
                    }
                )
        return overrides, applied

    return dict(factor_overrides or {}), applied


def simulate_index_prediction(
    *,
    macro_factors: dict[str, Any],
    factor_overrides: dict[str, float] | None = None,
    spot: float,
    bottom_up_return_pct: float,
    horizon_days: int | None = None,
    headline_return_pct: float | None = None,
    primary_factor: str | None = None,
    primary_shock_pct: float | None = None,
    cascade: bool = True,
    event_preset_id: str | None = None,
    event_impact_curves: list[dict[str, Any]] | None = None,
    cascade_calibration: CascadeCalibration | None = None,
    india_vix: float | None = None,
    force_heuristic_cascade: bool = False,
) -> dict[str, Any]:
    """Apply factor overrides and return updated forecast + attribution."""
    if spot <= 0:
        return {"error": "spot unavailable"}

    horizon = resolve_horizon(horizon_days)
    artifact = load_stored_model_artifact()

    resolved_overrides, cascade_applied = resolve_factor_overrides(
        macro_factors,
        factor_overrides=factor_overrides,
        primary_factor=primary_factor,
        primary_shock_pct=primary_shock_pct,
        cascade=cascade,
        event_preset_id=event_preset_id,
        event_impact_curves=event_impact_curves,
        cascade_calibration=cascade_calibration,
        india_vix=india_vix,
        force_heuristic_cascade=force_heuristic_cascade,
    )

    regime = classify_cascade_regime(india_vix=india_vix)
    cascade_method = "heuristic"
    if cascade_calibration and cascade_calibration.status == "ok" and not force_heuristic_cascade:
        cascade_method = "data_calibrated"

    merged = copy.deepcopy(macro_factors)
    for key, value in resolved_overrides.items():
        merged[key] = value

    baseline_macro_delta = cap_macro_delta(_predict_macro_delta(macro_factors, horizon, artifact))
    baseline_return = bottom_up_return_pct + baseline_macro_delta

    macro_delta = cap_macro_delta(_predict_macro_delta(merged, horizon, artifact))
    total_return = bottom_up_return_pct + macro_delta
    index_level = spot * (1.0 + total_return / 100.0)

    explanation = explain_macro_factors(
        merged,
        horizon=horizon,
        spot=spot,
        bottom_up_return_pct=bottom_up_return_pct,
        artifact=artifact,
    )
    if headline_return_pct is not None and not resolved_overrides:
        explanation["baseline_return_pct"] = round(headline_return_pct, 4)

    baseline_explanation = explain_macro_factors(
        macro_factors,
        horizon=horizon,
        spot=spot,
        bottom_up_return_pct=bottom_up_return_pct,
        artifact=artifact,
    )

    sensitivity = build_factor_sensitivity(
        merged,
        horizon=horizon,
        spot=spot,
        bottom_up_return_pct=bottom_up_return_pct,
        headline_return_pct=total_return,
        artifact=artifact,
        max_factors=12,
    )

    range_mae = float(artifact.mae if artifact else 1.5)
    forecast_path = build_forecast_path(
        spot=spot,
        baseline_return_pct=baseline_return,
        scenario_return_pct=total_return,
        horizon_days=horizon.days,
    )

    return {
        "expected_return_pct": round(total_return, 4),
        "baseline_return_pct": round(baseline_return, 4),
        "macro_delta_pct": round(macro_delta, 4),
        "bottom_up_return_pct": round(bottom_up_return_pct, 4),
        "index_level": round(index_level, 2),
        "baseline_index_level": round(spot * (1.0 + baseline_return / 100.0), 2),
        "range": {
            "low": round(spot * (1.0 + (total_return - range_mae) / 100.0), 2),
            "high": round(spot * (1.0 + (total_return + range_mae) / 100.0), 2),
        },
        "factor_explanation": explanation,
        "baseline_factor_explanation": baseline_explanation,
        "factor_sensitivity": sensitivity,
        "factor_overrides": resolved_overrides,
        "cascade_applied": cascade_applied,
        "cascade_method": cascade_method,
        "cascade_regime": regime,
        "cascade_calibration_as_of": (
            cascade_calibration.as_of if cascade_calibration else None
        ),
        "forecast_path": forecast_path,
        "horizon_days": horizon.days,
        "view": (
            "bullish"
            if total_return > 0.3
            else "bearish"
            if total_return < -0.3
            else "neutral"
        ),
    }
