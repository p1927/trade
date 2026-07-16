"""Blend heuristic priors with VAR-estimated cascade multipliers."""

from __future__ import annotations

from trade_integrations.dataflows.index_research.cascade.constants import DEFAULT_BLEND_ALPHA
from trade_integrations.dataflows.index_research.cascade.heuristic_rules import heuristic_rules_for
from trade_integrations.dataflows.index_research.cascade.types import CascadeSecondaryRule


def blend_rules(
    primary: str,
    *,
    var_rules: dict[str, list[CascadeSecondaryRule]] | None = None,
    alpha: float = DEFAULT_BLEND_ALPHA,
) -> list[CascadeSecondaryRule]:
    """Merge heuristic and VAR rules for one primary factor."""
    heur = {r.secondary: r for r in heuristic_rules_for(primary)}
    var_map = {r.secondary: r for r in (var_rules or {}).get(primary, [])}

    merged: list[CascadeSecondaryRule] = []
    for secondary, h_rule in heur.items():
        v_rule = var_map.get(secondary)
        if v_rule is None or v_rule.var_multiplier is None:
            merged.append(h_rule)
            continue

        h_mult = h_rule.heuristic_multiplier if h_rule.heuristic_multiplier is not None else h_rule.multiplier
        v_mult = v_rule.var_multiplier
        blended = alpha * h_mult + (1.0 - alpha) * v_mult
        merged.append(
            CascadeSecondaryRule(
                secondary=secondary,
                multiplier=blended,
                mode=h_rule.mode,
                source="blended",
                heuristic_multiplier=h_mult,
                var_multiplier=v_mult,
            )
        )

    return merged


def blend_all_rules(
    var_rules: dict[str, list[CascadeSecondaryRule]] | None,
    *,
    alpha: float = DEFAULT_BLEND_ALPHA,
    primaries: tuple[str, ...] | None = None,
) -> dict[str, list[CascadeSecondaryRule]]:
    """Blend rules for all primaries that have heuristic edges."""
    from trade_integrations.dataflows.index_research.cascade.heuristic_rules import (
        HEURISTIC_CASCADE_RULES,
    )

    keys = primaries or tuple(HEURISTIC_CASCADE_RULES.keys())
    return {primary: blend_rules(primary, var_rules=var_rules, alpha=alpha) for primary in keys}
