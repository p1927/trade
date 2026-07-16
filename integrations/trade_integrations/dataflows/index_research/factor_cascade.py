"""Backward-compatible facade — delegates to modular cascade package."""

from trade_integrations.dataflows.index_research.cascade import (
    build_cascade_overrides,
    load_cascade_calibration,
    overrides_from_event_preset,
    run_cascade_calibration,
)

__all__ = [
    "build_cascade_overrides",
    "load_cascade_calibration",
    "overrides_from_event_preset",
    "run_cascade_calibration",
]
