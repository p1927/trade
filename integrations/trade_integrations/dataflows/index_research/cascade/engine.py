"""Cascade engine — orchestrates rule provider + shock math (no VAR/heuristic coupling)."""

from __future__ import annotations

from typing import Any

from trade_integrations.dataflows.index_research.cascade.rule_provider import (
    CascadeRuleProvider,
    HeuristicRuleProvider,
    build_rule_provider,
)
from trade_integrations.dataflows.index_research.cascade.shock_math import (
    apply_secondary_shock,
    shock_primary_value,
)
from trade_integrations.dataflows.index_research.cascade.types import (
    CascadeCalibration,
    CascadeRegime,
)


def build_cascade_overrides(
    primary_factor: str,
    shock_pct: float,
    base_macro: dict[str, Any],
    *,
    cascade: bool = True,
    rule_provider: CascadeRuleProvider | None = None,
    calibration: CascadeCalibration | None = None,
    regime: CascadeRegime = "calm",
) -> tuple[dict[str, float], list[dict[str, Any]]]:
    """Build factor overrides from a primary shock using the injected rule provider."""
    provider = rule_provider or build_rule_provider(calibration, regime=regime)
    primary = primary_factor.strip()
    base_primary = float(base_macro.get(primary, 0.0) or 0.0)
    after_primary = shock_primary_value(base_primary, shock_pct, primary)

    overrides: dict[str, float] = {primary: after_primary}
    applied: list[dict[str, Any]] = [
        _applied_row(
            factor=primary,
            before=base_primary,
            after=after_primary,
            reason="primary_shock",
        )
    ]

    if not cascade or shock_pct == 0:
        return overrides, applied

    for rule in provider.rules_for(primary):
        secondary = rule.secondary
        if secondary == primary:
            continue
        base_sec = float(base_macro.get(secondary, 0.0) or 0.0)
        after_sec = apply_secondary_shock(base_sec, shock_pct, rule.multiplier, rule.mode)

        heuristic_after = None
        var_after = None
        if rule.heuristic_multiplier is not None:
            heuristic_after = apply_secondary_shock(
                base_sec, shock_pct, rule.heuristic_multiplier, rule.mode
            )
        if rule.var_multiplier is not None:
            var_after = apply_secondary_shock(base_sec, shock_pct, rule.var_multiplier, rule.mode)

        overrides[secondary] = after_sec
        applied.append(
            _applied_row(
                factor=secondary,
                before=base_sec,
                after=after_sec,
                reason=f"cascade_from_{primary}",
                source=rule.source,
                var_implied_after=var_after,
                heuristic_after=heuristic_after,
            )
        )

    return overrides, applied


def _applied_row(
    *,
    factor: str,
    before: float,
    after: float,
    reason: str,
    source: str | None = None,
    var_implied_after: float | None = None,
    heuristic_after: float | None = None,
) -> dict[str, Any]:
    row: dict[str, Any] = {
        "factor": factor,
        "before": round(before, 4),
        "after": round(after, 4),
        "reason": reason,
    }
    if source:
        row["source"] = source
    if var_implied_after is not None:
        row["var_implied_after"] = round(var_implied_after, 4)
    if heuristic_after is not None:
        row["heuristic_after"] = round(heuristic_after, 4)
    return row
