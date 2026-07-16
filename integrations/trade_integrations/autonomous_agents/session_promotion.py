"""Promote an orchestrator vibe session into a running autonomous agent session."""

from __future__ import annotations

from typing import Any

from src.session.models import Message
from src.session.orchestrator_profile import is_orchestrator_session


def promote_orchestrator_session(
    *,
    session_service: Any,
    orchestrator_session_id: str,
    agent_id: str,
    name: str,
    session_cfg: dict[str, Any],
) -> str:
    orch_sid = str(orchestrator_session_id or "").strip()
    if not orch_sid:
        raise ValueError("orchestrator_session_id is required")

    session = session_service.get_session(orch_sid)
    if session is None:
        raise ValueError(f"orchestrator session not found: {orch_sid}")
    if not is_orchestrator_session(session.config):
        raise ValueError(f"session is not orchestrator: {orch_sid}")

    session.config = dict(session_cfg)
    session.title = f"autonomous:{name}"
    session_service.store.update_session(session)

    transition = Message(
        session_id=orch_sid,
        role="system",
        content=(
            f"Autonomous agent **{name}** (`{agent_id}`) is now running. "
            "This chat continues as the agent session — scheduler and watch ticks will appear here."
        ),
    )
    session_service.store.append_message(transition)
    session_service.event_bus.emit(
        orch_sid,
        "message.received",
        {"message_id": transition.message_id, "role": "system", "content": transition.content},
    )
    session_service.event_bus.emit(
        orch_sid,
        "session.promoted",
        {"session_id": orch_sid, "agent_id": agent_id, "session_kind": "autonomous_agent"},
    )
    return orch_sid
