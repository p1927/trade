"""Factor cascade package — modular heuristic + VAR-calibrated spillovers."""

from trade_integrations.dataflows.index_research.cascade.calibration_store import (
    load_cascade_calibration,
    load_calibration_from_doc,
    save_cascade_calibration,
)
from trade_integrations.dataflows.index_research.cascade.calibrator import run_cascade_calibration
from trade_integrations.dataflows.index_research.cascade.engine import build_cascade_overrides
from trade_integrations.dataflows.index_research.cascade.event_presets import overrides_from_event_preset
from trade_integrations.dataflows.index_research.cascade.rule_provider import build_rule_provider
from trade_integrations.dataflows.index_research.cascade.types import CascadeCalibration

__all__ = [
    "CascadeCalibration",
    "build_cascade_overrides",
    "build_rule_provider",
    "load_cascade_calibration",
    "load_calibration_from_doc",
    "overrides_from_event_preset",
    "run_cascade_calibration",
    "save_cascade_calibration",
]
