"""Hub directory resolution without importing trade_integrations."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

_HUB_ENV = "TRADE_STACK_HUB_DIR"
_ROOT_ENV = "TRADE_STACK_ROOT"


def _trade_stack_root() -> Path:
    if custom := os.getenv(_ROOT_ENV, "").strip():
        return Path(custom).expanduser().resolve()
    # integrations/nautilus_openalgo_bridge/hub_paths.py -> repo root is parents[2]
    return Path(__file__).resolve().parents[2]


def get_hub_dir() -> Path:
    if custom := os.getenv(_HUB_ENV, "").strip():
        path = Path(custom).expanduser()
        if not path.is_absolute():
            path = _trade_stack_root() / path
        return path.resolve()
    return _trade_stack_root() / "reports" / "hub"


def agent_json_path(agent_id: str) -> Path:
    return get_hub_dir() / "_data" / "autonomous_agents" / f"{agent_id}.json"


def load_agent_json(agent_id: str) -> dict[str, Any]:
    path = agent_json_path(agent_id)
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def save_agent_json(agent: dict[str, Any]) -> dict[str, Any]:
    from datetime import datetime, timezone

    agent_id = str(agent.get("id") or "").strip()
    if not agent_id:
        raise ValueError("agent id is required")
    agent["updated_at"] = datetime.now(timezone.utc).isoformat()
    path = agent_json_path(agent_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(agent, indent=2, default=str), encoding="utf-8")
    return agent
