"""Sector factor OOS promotion gate (hub-persisted decision)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from trade_integrations.context.hub import get_hub_dir

SECTOR_FACTOR_KEYS: tuple[str, ...] = (
    "sector_breadth_price_7d",
    "sector_rel_strength_mean_7d",
    "bank_private_vs_psu_spread_7d",
)

_SECTOR_PROMOTION_GATE_PP = 3.0


def _sector_promotion_path() -> Path:
    return get_hub_dir() / "_data" / "index_factors" / "sector_promotion.json"


def load_sector_promotion_decision() -> dict[str, Any]:
    path = _sector_promotion_path()
    if not path.is_file():
        return {"promoted": False}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"promoted": False}
    return payload if isinstance(payload, dict) else {"promoted": False}


def save_sector_promotion_decision(decision: dict[str, Any]) -> Path:
    path = _sector_promotion_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(decision, indent=2), encoding="utf-8")
    return path


def promoted_sector_factor_keys() -> tuple[str, ...]:
    """Sector keys approved for Ridge training after OOS ablation gate."""
    if load_sector_promotion_decision().get("promoted"):
        return SECTOR_FACTOR_KEYS
    return ()


def sector_promotion_gate_pp() -> float:
    return _SECTOR_PROMOTION_GATE_PP
