"""Alpha Zoo bridge configuration (hub-persisted, env-gated)."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from trade_integrations.context.hub import get_hub_dir

_ENV_FLAG = "INDEX_ALPHA_BRIDGE_ENABLED"
_CONFIG_NAME = "alpha_zoo_config.json"

_DEFAULT_BASKET: tuple[str, ...] = (
    "alpha101_001",
    "alpha101_054",
    "qlib158_kmid",
)


def _config_path() -> Path:
    return get_hub_dir() / "_data" / "index_factors" / _CONFIG_NAME


def load_alpha_zoo_config() -> dict[str, Any]:
    """Load bridge config; return defaults when missing."""
    defaults: dict[str, Any] = {
        "enabled": False,
        "lookback_days": 90,
        "basket_alpha_ids": list(_DEFAULT_BASKET),
        "universe": "equity_in",
    }
    path = _config_path()
    if not path.is_file():
        return defaults
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return defaults
    if not isinstance(payload, dict):
        return defaults
    merged = {**defaults, **payload}
    basket = merged.get("basket_alpha_ids")
    if isinstance(basket, list):
        merged["basket_alpha_ids"] = [str(item).strip() for item in basket if str(item).strip()]
    else:
        merged["basket_alpha_ids"] = list(_DEFAULT_BASKET)
    return merged


def save_alpha_zoo_config(config: dict[str, Any]) -> Path:
    path = _config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(config, indent=2, default=str), encoding="utf-8")
    return path


def is_bridge_enabled() -> bool:
    """True when env flag or hub config enables alpha bridge compute."""
    env = os.environ.get(_ENV_FLAG, "").strip().lower()
    if env in {"1", "true", "yes", "on"}:
        return True
    if env in {"0", "false", "no", "off"}:
        return False
    return bool(load_alpha_zoo_config().get("enabled"))


def basket_alpha_ids() -> tuple[str, ...]:
    cfg = load_alpha_zoo_config()
    ids = cfg.get("basket_alpha_ids") or list(_DEFAULT_BASKET)
    return tuple(str(item).strip() for item in ids if str(item).strip())


def lookback_days() -> int:
    try:
        return max(30, int(load_alpha_zoo_config().get("lookback_days") or 90))
    except (TypeError, ValueError):
        return 90
