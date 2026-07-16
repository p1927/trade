"""Persist auto paper trading session state under hub storage."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from trade_integrations.context.hub import get_hub_dir


def _session_path() -> Path:
    return get_hub_dir() / "_data" / "auto_paper" / "session.json"


def load_session() -> dict[str, Any]:
    path = _session_path()
    if not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    return payload if isinstance(payload, dict) else {}


def save_session(session: dict[str, Any]) -> dict[str, Any]:
    path = _session_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    session["updated_at"] = datetime.now(timezone.utc).isoformat()
    path.write_text(json.dumps(session, indent=2, default=str), encoding="utf-8")
    return session


def start_session(*, budget_inr: float, watchlist: list[str]) -> dict[str, Any]:
    session = load_session()
    session.update(
        {
            "enabled": True,
            "autonomous": True,
            "agent_mode": True,
            "started_at": datetime.now(timezone.utc).isoformat(),
            "budget_inr": budget_inr,
            "watchlist": watchlist,
            "starting_balance": session.get("starting_balance"),
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
    return save_session(session)


def set_vibe_session_id(session_id: str) -> dict[str, Any]:
    session = load_session()
    session["vibe_session_id"] = session_id
    return save_session(session)


def get_vibe_session_id() -> str | None:
    session = load_session()
    value = session.get("vibe_session_id")
    return str(value).strip() if value else None


def stop_session() -> dict[str, Any]:
    session = load_session()
    session["enabled"] = False
    session["stopped_at"] = datetime.now(timezone.utc).isoformat()
    return save_session(session)


def record_tick_result(result: dict[str, Any]) -> dict[str, Any]:
    session = load_session()
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
    return save_session(session)


def _tick_summary(result: dict[str, Any]) -> dict[str, Any]:
    return {
        "status": result.get("status"),
        "actions": result.get("actions"),
        "halt_reason": result.get("halt_reason"),
    }
