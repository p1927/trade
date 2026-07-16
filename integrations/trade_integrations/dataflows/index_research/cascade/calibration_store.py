"""Persist and load cascade calibration artifacts from the hub."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from trade_integrations.context.hub import get_hub_dir
from trade_integrations.dataflows.index_research.cascade.types import CascadeCalibration

_CALIBRATION_FILENAME = "cascade_calibration.json"


def calibration_path(ticker: str = "NIFTY") -> Path:
    key = ticker.strip().upper()
    return get_hub_dir() / key / "index_research" / _CALIBRATION_FILENAME


def save_cascade_calibration(
    calibration: CascadeCalibration,
    *,
    ticker: str = "NIFTY",
) -> Path:
    path = calibration_path(ticker)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(calibration.to_dict(), indent=2), encoding="utf-8")
    return path


def load_cascade_calibration(ticker: str = "NIFTY") -> CascadeCalibration | None:
    path = calibration_path(ticker)
    if not path.is_file():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return CascadeCalibration.from_dict(raw)


def load_calibration_from_doc(doc: Any) -> CascadeCalibration | None:
    """Prefer embedded hub doc field, fall back to sidecar JSON."""
    if doc is None:
        return load_cascade_calibration()
    embedded = getattr(doc, "cascade_calibration", None) or {}
    if embedded:
        parsed = CascadeCalibration.from_dict(embedded)
        if parsed:
            return parsed
    ticker = getattr(doc, "ticker", None) or "NIFTY"
    return load_cascade_calibration(str(ticker))
