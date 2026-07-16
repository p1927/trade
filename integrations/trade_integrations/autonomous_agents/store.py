"""Persist autonomous agent instances under hub storage."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from trade_integrations.context.hub import get_hub_dir

_AGENT_DIR = "autonomous_agents"
_PROPOSAL_DIR = "proposals"
_ORCHESTRATOR_FILE = "orchestrator.json"


def _agents_root() -> Path:
    root = get_hub_dir() / "_data" / _AGENT_DIR
    root.mkdir(parents=True, exist_ok=True)
    return root


def _agent_path(agent_id: str) -> Path:
    return _agents_root() / f"{agent_id}.json"


def _proposal_path(proposal_id: str) -> Path:
    path = _agents_root() / _PROPOSAL_DIR / f"{proposal_id}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def new_agent_id() -> str:
    return f"aa_{uuid.uuid4().hex}"


def new_proposal_id() -> str:
    return f"aap_{uuid.uuid4().hex}"


def load_agent(agent_id: str) -> dict[str, Any]:
    path = _agent_path(agent_id)
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def save_agent(agent: dict[str, Any]) -> dict[str, Any]:
    agent_id = str(agent.get("id") or "").strip()
    if not agent_id:
        raise ValueError("agent id is required")
    agent["updated_at"] = datetime.now(timezone.utc).isoformat()
    path = _agent_path(agent_id)
    path.write_text(json.dumps(agent, indent=2, default=str), encoding="utf-8")
    return agent


def delete_agent(agent_id: str) -> bool:
    path = _agent_path(agent_id)
    if not path.is_file():
        return False
    path.unlink()
    return True


def list_agents() -> list[dict[str, Any]]:
    root = _agents_root()
    agents: list[dict[str, Any]] = []
    for path in sorted(root.glob("aa_*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(data, dict) and data.get("id"):
            agents.append(data)
    agents.sort(key=lambda a: str(a.get("created_at") or ""), reverse=True)
    return agents


def get_agent(agent_id: str) -> dict[str, Any] | None:
    data = load_agent(agent_id)
    return data or None


def save_proposal(proposal: dict[str, Any]) -> dict[str, Any]:
    proposal_id = str(proposal.get("proposal_id") or "").strip()
    if not proposal_id:
        raise ValueError("proposal_id is required")
    now = datetime.now(timezone.utc).isoformat()
    proposal.setdefault("created_at", now)
    proposal["updated_at"] = now
    _proposal_path(proposal_id).write_text(
        json.dumps(proposal, indent=2, default=str),
        encoding="utf-8",
    )
    return proposal


def load_proposal(proposal_id: str) -> dict[str, Any] | None:
    path = _proposal_path(proposal_id)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def load_latest_proposal_for_orchestrator(orchestrator_session_id: str) -> dict[str, Any] | None:
    """Newest uncommitted, non-expired proposal for an orchestrator vibe session."""
    import time

    orch = str(orchestrator_session_id or "").strip()
    if not orch:
        return None
    root = _agents_root() / _PROPOSAL_DIR
    if not root.is_dir():
        return None
    now_ms = int(time.time() * 1000)
    best: dict[str, Any] | None = None
    best_created = ""
    for path in root.glob("aap_*.json"):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(data, dict):
            continue
        if str(data.get("orchestrator_session_id") or "") != orch:
            continue
        if data.get("committed_agent_id"):
            continue
        expires = int(data.get("expires_at_ms") or 0)
        if expires and now_ms > expires:
            continue
        created = str(data.get("created_at") or "")
        if created >= best_created:
            best = data
            best_created = created
    if best is not None and str(best.get("orchestrator_session_id") or "") != orch:
        best["session_id"] = orch
    elif best is not None:
        best.setdefault("session_id", orch)
    return best


def delete_proposal(proposal_id: str) -> bool:
    path = _proposal_path(proposal_id)
    if not path.is_file():
        return False
    path.unlink()
    return True


def get_orchestrator_meta() -> dict[str, Any]:
    path = _agents_root() / _ORCHESTRATOR_FILE
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def save_orchestrator_meta(meta: dict[str, Any]) -> dict[str, Any]:
    path = _agents_root() / _ORCHESTRATOR_FILE
    meta["updated_at"] = datetime.now(timezone.utc).isoformat()
    path.write_text(json.dumps(meta, indent=2, default=str), encoding="utf-8")
    return meta


def clear_orchestrator_meta() -> None:
    path = _agents_root() / _ORCHESTRATOR_FILE
    if path.is_file():
        path.unlink()
