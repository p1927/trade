"""Regime-dependent scaling for cascade spillovers."""

from __future__ import annotations

from trade_integrations.dataflows.index_research.cascade.types import CascadeRegime, CascadeSecondaryRule


def classify_cascade_regime(*, india_vix: float | None) -> CascadeRegime:
    """Map India VIX to cascade regime bucket."""
    if india_vix is None:
        return "calm"
    if india_vix >= 20:
        return "crisis"
    if india_vix >= 16:
        return "elevated"
    return "calm"


def regime_scale(regime: CascadeRegime) -> float:
    """Multiplier applied to secondary cascade strengths."""
    return {"calm": 1.0, "elevated": 1.1, "crisis": 1.25}[regime]


def scale_rules(
    rules: list[CascadeSecondaryRule],
    regime: CascadeRegime,
) -> list[CascadeSecondaryRule]:
    """Scale secondary multipliers for elevated/crisis regimes."""
    factor = regime_scale(regime)
    if factor == 1.0:
        return rules

    scaled: list[CascadeSecondaryRule] = []
    for rule in rules:
        scaled.append(
            CascadeSecondaryRule(
                secondary=rule.secondary,
                multiplier=rule.multiplier * factor,
                mode=rule.mode,
                source=rule.source,
                heuristic_multiplier=(
                    rule.heuristic_multiplier * factor if rule.heuristic_multiplier is not None else None
                ),
                var_multiplier=rule.var_multiplier * factor if rule.var_multiplier is not None else None,
            )
        )
    return scaled
