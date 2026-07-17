"""OOS promotion gate for Alpha Zoo composite Ridge factors."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from trade_integrations.context.hub import get_hub_dir

ALPHA_ZOO_FACTOR_KEYS: tuple[str, ...] = (
    "alpha_zoo_ls_spread",
    "alpha_zoo_breadth",
    "alpha_zoo_momentum_consensus",
    "alpha_zoo_dispersion",
)

_ALPHA_PROMOTION_GATE_PP = 3.0


def _promotion_path() -> Path:
    return get_hub_dir() / "_data" / "index_factors" / "alpha_promotion.json"


def load_alpha_promotion_decision() -> dict[str, Any]:
    path = _promotion_path()
    if not path.is_file():
        return {"promoted": False}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"promoted": False}
    return payload if isinstance(payload, dict) else {"promoted": False}


def save_alpha_promotion_decision(decision: dict[str, Any]) -> Path:
    path = _promotion_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(decision, indent=2), encoding="utf-8")
    return path


def promoted_alpha_zoo_factor_keys() -> tuple[str, ...]:
    """Composite keys approved for Ridge training after OOS ablation gate."""
    if load_alpha_promotion_decision().get("promoted"):
        return ALPHA_ZOO_FACTOR_KEYS
    return ()


def alpha_promotion_gate_pp() -> float:
    return _ALPHA_PROMOTION_GATE_PP
