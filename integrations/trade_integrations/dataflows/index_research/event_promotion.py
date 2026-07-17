"""T0 headline event flags — OOS promotion gate."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from trade_integrations.context.hub import get_hub_dir

EVENT_FLAG_KEYS: tuple[str, ...] = (
    "geopolitical_headline_flag",
    "oil_headline_flag",
)

_EVENT_PROMOTION_GATE_PP = 3.0


def _event_promotion_path() -> Path:
    return get_hub_dir() / "_data" / "index_factors" / "event_promotion.json"


def load_event_promotion_decision() -> dict[str, Any]:
    path = _event_promotion_path()
    if not path.is_file():
        return {"promoted": False}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"promoted": False}
    return payload if isinstance(payload, dict) else {"promoted": False}


def save_event_promotion_decision(decision: dict[str, Any]) -> Path:
    path = _event_promotion_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(decision, indent=2), encoding="utf-8")
    return path


def promoted_event_factor_keys() -> tuple[str, ...]:
    if load_event_promotion_decision().get("promoted"):
        return EVENT_FLAG_KEYS
    return ()


def event_promotion_gate_pp() -> float:
    return _EVENT_PROMOTION_GATE_PP
