"""Read Nautilus multi-agent registry without trade_integrations (Nautilus venv safe)."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


def trade_root() -> Path:
    raw = os.getenv("TRADE_STACK_ROOT", "").strip()
    if raw:
        return Path(raw).expanduser().resolve()
    return Path(__file__).resolve().parents[2]


def registry_path() -> Path:
    return trade_root() / "log" / "nautilus-watch.agents.json"


def load_registry_payload() -> dict[str, Any]:
    path = registry_path()
    if not path.is_file():
        return {"node_pid": None, "agents": [], "updated_at": None}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"node_pid": None, "agents": [], "updated_at": None}
    if not isinstance(payload, dict):
        return {"node_pid": None, "agents": [], "updated_at": None}
    agents = payload.get("agents")
    if not isinstance(agents, list):
        agents = []
    return {
        "node_pid": payload.get("node_pid"),
        "agents": [row for row in agents if isinstance(row, dict)],
        "updated_at": payload.get("updated_at"),
        "node_agent_ids": payload.get("node_agent_ids"),
    }


def read_registry_agent_ids() -> list[str]:
    ids = [
        str(row.get("agent_id") or "").strip()
        for row in load_registry_payload().get("agents") or []
        if str(row.get("agent_id") or "").strip()
    ]
    return ids


def read_registry_agents() -> list[dict[str, Any]]:
    return list(load_registry_payload().get("agents") or [])


def registry_agent_market(agent_id: str) -> str:
    agent_id = str(agent_id or "").strip()
    for row in read_registry_agents():
        if str(row.get("agent_id") or "") == agent_id:
            return str(row.get("market") or "IN").upper()
    return "IN"
