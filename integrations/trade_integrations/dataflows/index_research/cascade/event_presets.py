"""Event preset shocks — separate from heuristic/VAR cascade paths."""

from __future__ import annotations

from typing import Any

from trade_integrations.dataflows.index_research.cascade.constants import ABSOLUTE_PRIMARY


def overrides_from_event_preset(
    event_impact_curves: list[dict[str, Any]],
    preset_id: str,
    base_macro: dict[str, Any],
    *,
    progress: float = 1.0,
) -> tuple[dict[str, float], list[dict[str, Any]]]:
    """Apply a precomputed event_impact_curves entry at partial or full strength."""
    key = preset_id.strip()
    curve = None
    for item in event_impact_curves:
        eid = f"{item.get('event')}|{item.get('outcome')}"
        if eid == key:
            curve = item
            break
    if curve is None:
        return {}, []

    shocks = curve.get("factor_shocks") or {}
    if not shocks:
        return {}, []

    t = max(0.0, min(1.0, progress))
    overrides: dict[str, float] = {}
    applied: list[dict[str, Any]] = []
    primary = str(curve.get("primary_factor") or max(shocks.keys(), key=lambda k: abs(float(shocks[k]))))

    for factor, shock in shocks.items():
        base = float(base_macro.get(factor, 0.0) or 0.0)
        partial = float(shock) * t
        if factor in ABSOLUTE_PRIMARY:
            after = base + partial
        else:
            after = base * (1.0 + partial) if base else partial
        overrides[factor] = after
        applied.append(
            {
                "factor": factor,
                "before": round(base, 4),
                "after": round(after, 4),
                "reason": f"event_preset_{key}" if factor != primary else f"event_preset_{key}_primary",
                "source": "heuristic",
            }
        )

    return overrides, applied
