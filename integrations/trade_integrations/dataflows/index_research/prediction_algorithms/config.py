"""Feature flags for optional forecast lab (removable sidecar)."""

from __future__ import annotations

import os
from typing import Literal

LabMode = Literal["log", "combine"]


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def lab_enabled() -> bool:
    return _env_bool("INDEX_PREDICTION_LAB_ENABLED", default=False)


def lab_mode() -> LabMode:
    raw = (os.environ.get("INDEX_PREDICTION_LAB_MODE") or "log").strip().lower()
    return "combine" if raw == "combine" else "log"


def default_combiner_id() -> str:
    return (os.environ.get("INDEX_PREDICTION_COMBINER") or "quant_only").strip() or "quant_only"


def scoreboard_auto_refresh() -> bool:
    """Refresh walk-forward scoreboard after index research when lab is on."""
    if not lab_enabled():
        return False
    return _env_bool("INDEX_PREDICTION_SCOREBOARD_AUTO_REFRESH", default=True)
