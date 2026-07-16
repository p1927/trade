"""Persist auto paper trading session state under hub storage (per-agent + legacy)."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from trade_integrations.context.hub import get_hub_dir


def _auto_paper_dir() -> Path:
    return get_hub_dir() / "_data" / "auto_paper"


def _sessions_dir() -> Path:
    return _auto_paper_dir() / "sessions"


def _legacy_session_path() -> Path:
    return _auto_paper_dir() / "session.json"


def _active_pointer_path() -> Path:
    return _auto_paper_dir() / "active_agent_id.txt"


def _session_path_for(agent_id: str) -> Path:
    safe = "".join(c for c in agent_id if c.isalnum() or c in "_-")
    return _sessions_dir() / f"{safe}.json"


def _read_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _write_json(path: Path, session: dict[str, Any]) -> dict[str, Any]:
    path.parent.mkdir(parents=True, exist_ok=True)
    session["updated_at"] = datetime.now(timezone.utc).isoformat()
    path.write_text(json.dumps(session, indent=2, default=str), encoding="utf-8")
    return session


def _resolve_agent_id(autonomous_agent_id: str | None, session: dict[str, Any] | None = None) -> str | None:
    if autonomous_agent_id:
        return autonomous_agent_id.strip() or None
    if session:
        stored = str(session.get("autonomous_agent_id") or "").strip()
        if stored:
            return stored
    pointer = _active_pointer_path()
    if pointer.is_file():
        return pointer.read_text(encoding="utf-8").strip() or None
    return None


def load_session(*, autonomous_agent_id: str | None = None) -> dict[str, Any]:
    """Load paper session for an agent, or the active/legacy session."""
    agent_id = _resolve_agent_id(autonomous_agent_id)
    if agent_id:
        per_agent = _read_json(_session_path_for(agent_id))
        if per_agent:
            return per_agent
    legacy = _read_json(_legacy_session_path())
    if legacy and (not agent_id or str(legacy.get("autonomous_agent_id") or "") == agent_id):
        return legacy
    if agent_id:
        return _read_json(_session_path_for(agent_id))
    return legacy


def save_session(session: dict[str, Any], *, autonomous_agent_id: str | None = None) -> dict[str, Any]:
    agent_id = _resolve_agent_id(autonomous_agent_id, session)
    if agent_id:
        session["autonomous_agent_id"] = agent_id
        _write_json(_session_path_for(agent_id), session)
        _active_pointer_path().write_text(agent_id, encoding="utf-8")
        return session
    return _write_json(_legacy_session_path(), session)


def start_session(
    *,
    budget_inr: float,
    watchlist: list[str],
    autonomous_agent_id: str | None = None,
) -> dict[str, Any]:
    agent_id = str(autonomous_agent_id or "").strip() or None
    session = load_session(autonomous_agent_id=agent_id) if agent_id else load_session()
    prior_agent = str(session.get("autonomous_agent_id") or "").strip()
    new_agent = agent_id or ""
    reset_baseline = bool(new_agent and new_agent != prior_agent) or not session.get("enabled")

    starting_balance = None if reset_baseline else session.get("starting_balance")
    session.update(
        {
            "enabled": True,
            "autonomous": True,
            "agent_mode": True,
            "started_at": datetime.now(timezone.utc).isoformat(),
            "budget_inr": budget_inr,
            "watchlist": watchlist,
            "starting_balance": starting_balance,
            "pnl_basis": "budget_inr" if reset_baseline else session.get("pnl_basis"),
            "daily_realized_pnl": 0.0,
            "trades_today": 0,
            "halted": False,
            "halt_reason": None,
            "last_tick_at": None,
            "last_tick": None,
            "tick_history": session.get("tick_history") or [],
            "vibe_session_id": session.get("vibe_session_id"),
            "lifecycle": session.get("lifecycle"),
            "decisions": session.get("decisions") or [],
        }
    )
    if new_agent:
        session["autonomous_agent_id"] = new_agent
    return save_session(session, autonomous_agent_id=new_agent)


def set_vibe_session_id(session_id: str, *, autonomous_agent_id: str | None = None) -> dict[str, Any]:
    session = load_session(autonomous_agent_id=autonomous_agent_id)
    session["vibe_session_id"] = session_id
    return save_session(session, autonomous_agent_id=autonomous_agent_id)


def get_vibe_session_id(*, autonomous_agent_id: str | None = None) -> str | None:
    session = load_session(autonomous_agent_id=autonomous_agent_id)
    value = session.get("vibe_session_id")
    return str(value).strip() if value else None


def stop_all_paper_sessions() -> int:
    """Disable legacy and per-agent paper sessions; return count stopped."""
    stopped = 0
    legacy = _read_json(_legacy_session_path())
    if legacy.get("enabled"):
        legacy["enabled"] = False
        legacy["stopped_at"] = datetime.now(timezone.utc).isoformat()
        _write_json(_legacy_session_path(), legacy)
        stopped += 1
    sessions_dir = _sessions_dir()
    if sessions_dir.is_dir():
        for path in sessions_dir.glob("*.json"):
            row = _read_json(path)
            if row.get("enabled"):
                row["enabled"] = False
                row["stopped_at"] = datetime.now(timezone.utc).isoformat()
                _write_json(path, row)
                stopped += 1
    _active_pointer_path().unlink(missing_ok=True)
    return stopped


def stop_session(*, autonomous_agent_id: str | None = None) -> dict[str, Any]:
    session = load_session(autonomous_agent_id=autonomous_agent_id)
    session["enabled"] = False
    session["stopped_at"] = datetime.now(timezone.utc).isoformat()
    saved = save_session(session, autonomous_agent_id=autonomous_agent_id)
    agent_id = _resolve_agent_id(autonomous_agent_id, session)
    if agent_id and _active_pointer_path().is_file():
        active = _active_pointer_path().read_text(encoding="utf-8").strip()
        if active == agent_id:
            _active_pointer_path().unlink(missing_ok=True)
    return saved


def record_tick_result(result: dict[str, Any], *, autonomous_agent_id: str | None = None) -> dict[str, Any]:
    session = load_session(autonomous_agent_id=autonomous_agent_id)
    session["last_tick_at"] = datetime.now(timezone.utc).isoformat()
    session["last_tick"] = result
    history = list(session.get("tick_history") or [])
    history.append({"at": session["last_tick_at"], "summary": _tick_summary(result)})
    session["tick_history"] = history[-100:]
    if result.get("halted"):
        session["halted"] = True
        session["halt_reason"] = result.get("halt_reason")
    if result.get("trade_executed"):
        session["trades_today"] = int(session.get("trades_today") or 0) + 1
    return save_session(session, autonomous_agent_id=autonomous_agent_id)


def _tick_summary(result: dict[str, Any]) -> dict[str, Any]:
    return {
        "status": result.get("status"),
        "actions": result.get("actions"),
        "halt_reason": result.get("halt_reason"),
    }
