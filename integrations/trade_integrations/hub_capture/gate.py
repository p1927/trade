"""Capture gate — decide whether a series should be persisted for an entity."""

from __future__ import annotations

from typing import Any

from trade_integrations.hub_capture.registry import get_entity, load_registry

SERIES_TO_GROUP: dict[str, str] = {
    "derivatives_chain": "derivatives",
    "participant_oi": "derivatives",
    "flows": "flows",
    "vix": "vol",
    "ticks": "ticks",
    "news_verified": "flows",
    "ohlcv_daily": "derivatives",
    "quotes": "derivatives",
}


def should_capture(entity_id: str, series_type: str, *, registry: dict[str, Any] | None = None) -> bool:
    """Return True when registry enables capture for this entity and series group."""
    reg = registry or load_registry(create=False)
    entity = get_entity(entity_id, registry=reg)
    if not entity or not entity.get("capture_enabled"):
        return False
    group = SERIES_TO_GROUP.get(series_type.strip().lower(), series_type.strip().lower())
    if group == "ticks":
        return "derivatives" in (entity.get("factor_groups") or [])
    allowed = {str(g).lower() for g in (entity.get("factor_groups") or [])}
    return group in allowed
