"""Selective hub data capture — registry, gate, writers, rollup."""

from trade_integrations.hub_capture.gate import should_capture
from trade_integrations.hub_capture.registry import (
    build_capture_stats,
    build_factor_tree,
    default_registry,
    get_entity,
    is_capture_enabled,
    load_registry,
    save_registry,
    update_entity,
)

__all__ = [
    "build_capture_stats",
    "build_factor_tree",
    "default_registry",
    "get_entity",
    "is_capture_enabled",
    "load_registry",
    "save_registry",
    "should_capture",
    "update_entity",
]
