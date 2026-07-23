"""Persist autonomous agent instances under hub storage."""

from __future__ import annotations

import json
import os
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from trade_integrations.context import hub as hub_context

_AGENT_DIR = "autonomous_agents"
_PROPOSAL_DIR = "proposals"
_ORCHESTRATOR_FILE = "orchestrator.json"


def _agents_root() -> Path:
    root = hub_context.get_hub_dir() / "_data" / _AGENT_DIR
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
    from trade_integrations.autonomous_agents.agent_schema import ensure_agent_lifecycle
    from trade_integrations.autonomous_agents.plan_approval import ensure_plan_approval_record

    data = load_agent(agent_id)
    if not data:
        return None
    data = ensure_agent_lifecycle(data, persist=True)
    return ensure_plan_approval_record(data, persist=True)


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
        if data.get("superseded"):
            continue
        expires = int(data.get("expires_at_ms") or 0)
        if expires and now_ms > expires:
            continue
        created = str(data.get("created_at") or "")
        if created >= best_created:
            best = data
            best_created = created
    if best is None:
        return None
    out = dict(best)
    out.setdefault("orchestrator_session_id", orch)
    out.setdefault("session_id", orch)
    return out


def mark_superseded_proposals(orchestrator_session_id: str, *, except_proposal_id: str) -> int:
    """Mark prior uncommitted proposals in the same orchestrator session as superseded."""
    orch = str(orchestrator_session_id or "").strip()
    keep = str(except_proposal_id or "").strip()
    if not orch:
        return 0
    root = _agents_root() / _PROPOSAL_DIR
    if not root.is_dir():
        return 0
    now = datetime.now(timezone.utc).isoformat()
    count = 0
    for path in root.glob("aap_*.json"):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(data, dict):
            continue
        if str(data.get("orchestrator_session_id") or "") != orch:
            continue
        pid = str(data.get("proposal_id") or path.stem)
        if pid == keep or data.get("committed_agent_id") or data.get("superseded"):
            continue
        data["superseded"] = True
        data["superseded_at"] = now
        data["superseded_by"] = keep
        path.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")
        count += 1
    return count


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


def _proposal_commit_lock_path(proposal_id: str) -> Path:
    return _proposal_path(proposal_id).with_suffix(".commit.lock")


_COMMIT_LOCK_TTL_SEC = 300.0


def _lock_holder_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _commit_lock_is_stale(lock_path: Path) -> bool:
    """True when lock file is orphaned (dead PID) or exceeded TTL."""
    if not lock_path.is_file():
        return False
    try:
        age_sec = time.time() - lock_path.stat().st_mtime
        if age_sec > _COMMIT_LOCK_TTL_SEC:
            return True
        raw = lock_path.read_text(encoding="utf-8").strip()
        holder_pid = int(raw)
    except (OSError, ValueError):
        return True
    return not _lock_holder_alive(holder_pid)


@contextmanager
def acquire_proposal_commit_lock(proposal_id: str) -> Iterator[None]:
    """Exclusive lock for proposal commit — prevents double-commit races."""
    pid = str(proposal_id or "").strip()
    if not pid:
        raise ValueError("proposal_id is required for commit lock")
    lock_path = _proposal_commit_lock_path(pid)
    lock_path.parent.mkdir(parents=True, exist_ok=True)

    def _try_acquire() -> int | None:
        fd: int | None = None
        try:
            fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(fd, str(os.getpid()).encode())
            return fd
        except FileExistsError:
            return None

    fd = _try_acquire()
    if fd is None:
        if _commit_lock_is_stale(lock_path):
            lock_path.unlink(missing_ok=True)
            fd = _try_acquire()
        if fd is None:
            raise ValueError("commit already in progress")
    if fd is not None:
        os.close(fd)
    try:
        yield
    finally:
        lock_path.unlink(missing_ok=True)


def clear_orchestrator_meta(orchestrator_session_id: str | None = None) -> None:
    """Clear orchestrator meta globally or for a specific session."""
    path = _agents_root() / _ORCHESTRATOR_FILE
    if not path.is_file():
        return
    if orchestrator_session_id is None:
        path.unlink()
        return
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        path.unlink(missing_ok=True)
        return
    if not isinstance(data, dict):
        path.unlink(missing_ok=True)
        return
    active = str(data.get("active_orchestrator_session_id") or "")
    if active == str(orchestrator_session_id).strip():
        path.unlink(missing_ok=True)


def get_active_orchestrator_session_id() -> str | None:
    meta = get_orchestrator_meta()
    sid = str(meta.get("active_orchestrator_session_id") or "").strip()
    return sid or None


def set_active_orchestrator_session_id(session_id: str) -> dict[str, Any]:
    return save_orchestrator_meta({"active_orchestrator_session_id": str(session_id).strip()})


def find_agent_by_vibe_session(
    vibe_session_id: str,
    *,
    status: str | None = None,
) -> dict[str, Any] | None:
    """Find agent record bound to a vibe session id."""
    sid = str(vibe_session_id or "").strip()
    if not sid:
        return None
    want_status = str(status or "").strip().lower()
    for agent in list_agents():
        if str(agent.get("vibe_session_id") or "") != sid:
            continue
        if want_status and str(agent.get("status") or "").lower() != want_status:
            continue
        return agent
    return None


def create_draft_agent(*, session_service: Any) -> dict[str, Any]:
    """Create a draft agent card and fresh orchestrator vibe session."""
    from trade_integrations.autonomous_agents.turns import build_orchestrator_system_note

    if session_service is None:
        raise ValueError("session runtime not enabled")

    agent_id = new_agent_id()
    now = datetime.now(timezone.utc).isoformat()
    session = session_service.create_session(
        title=f"autonomous:draft:{agent_id[:12]}",
        config={
            "session_kind": "autonomous_orchestrator",
            "orchestrator": True,
            "draft_agent_id": agent_id,
            "system_note": build_orchestrator_system_note(),
        },
    )
    agent: dict[str, Any] = {
        "id": agent_id,
        "type": "autonomous_agent.instance",
        "name": "New agent draft",
        "status": "draft",
        "pause_reason": None,
        "infra_pending": [],
        "infra_last_attempt_at": None,
        "vibe_session_id": session.session_id,
        "symbols": [],
        "execution_market": None,
        "execution_backend": None,
        "connector_profile_id": None,
        "mandate": "",
        "mandate_config": {},
        "watch_spec": {},
        "constraints": {},
        "schedules": {},
        "alert_rules": {},
        "thesis": {},
        "user_guidance": [],
        "last_watch_at": None,
        "last_full_reasoning_at": None,
        "last_revision_at": None,
        "streaming": False,
        "bootstrap_status": None,
        "proposal_id": None,
        "orchestrator_session_id": session.session_id,
        "created_at": now,
    }
    save_agent(agent)
    return {
        "status": "ok",
        "agent_id": agent_id,
        "session_id": session.session_id,
        "agent": agent,
    }


def backfill_orphan_orchestrator_session(*, session_service: Any) -> dict[str, Any] | None:
    """Migrate legacy orchestrator.json active session into a draft agent if unbound."""
    if session_service is None:
        return None
    active_sid = get_active_orchestrator_session_id()
    if not active_sid:
        return None
    if find_agent_by_vibe_session(active_sid):
        return None
    existing = session_service.get_session(active_sid)
    if existing is None:
        clear_orchestrator_meta(active_sid)
        return None
    try:
        from src.session.orchestrator_profile import is_orchestrator_session
    except ImportError:
        is_orchestrator_session = lambda cfg: bool((cfg or {}).get("orchestrator"))  # noqa: E731

    if not is_orchestrator_session(existing.config):
        return None

    agent_id = new_agent_id()
    now = datetime.now(timezone.utc).isoformat()
    draft_agent_id = str((existing.config or {}).get("draft_agent_id") or agent_id)
    agent: dict[str, Any] = {
        "id": draft_agent_id,
        "type": "autonomous_agent.instance",
        "name": existing.title or "Agent draft",
        "status": "draft",
        "pause_reason": None,
        "infra_pending": [],
        "infra_last_attempt_at": None,
        "vibe_session_id": active_sid,
        "symbols": [],
        "execution_market": None,
        "execution_backend": None,
        "connector_profile_id": None,
        "mandate": "",
        "mandate_config": {},
        "watch_spec": {},
        "constraints": {},
        "schedules": {},
        "alert_rules": {},
        "thesis": {},
        "user_guidance": [],
        "last_watch_at": None,
        "last_full_reasoning_at": None,
        "last_revision_at": None,
        "streaming": False,
        "bootstrap_status": None,
        "proposal_id": None,
        "orchestrator_session_id": active_sid,
        "created_at": now,
    }
    save_agent(agent)
    return {"status": "ok", "agent_id": draft_agent_id, "session_id": active_sid, "agent": agent, "backfilled": True}
