"""Pure math for applying primary and secondary factor shocks."""

from __future__ import annotations

from trade_integrations.dataflows.index_research.cascade.constants import ABSOLUTE_PRIMARY


def shock_primary_value(base: float, shock_pct: float, factor: str) -> float:
    """Apply a user shock to the primary factor level."""
    if factor in ABSOLUTE_PRIMARY:
        return base + (shock_pct / 100.0) * max(abs(base), 1.0)
    if base == 0:
        return shock_pct / 100.0
    return base * (1.0 + shock_pct / 100.0)


def apply_secondary_shock(
    base: float,
    shock_pct: float,
    multiplier: float,
    mode: str,
) -> float:
    """Apply a cascade secondary shock given primary shock percent."""
    if mode == "absolute":
        return base + shock_pct * multiplier
    if base == 0:
        return shock_pct / 100.0 * multiplier
    return base * (1.0 + (shock_pct / 100.0) * multiplier)
