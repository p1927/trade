"""Promote an orchestrator vibe session into a running autonomous agent session."""

from __future__ import annotations

from typing import Any

from src.session.models import Message
from src.session.orchestrator_profile import is_orchestrator_session


def _mandate_divider_content(*, name: str, agent_id: str, proposal: dict[str, Any] | None) -> str:
    if not proposal:
        return (
            f"**Orchestrator phase complete** — agent **{name}** (`{agent_id}`) mandate is now locked. "
            "Ignore earlier orchestrator proposals for other symbols."
        )

    symbols = ", ".join(proposal.get("symbols") or [])
    mc = dict(proposal.get("mandate_config") or {})
    instruments = ", ".join(mc.get("allowed_instruments") or ["options"])
    holding = mc.get("holding_period") or "multi_day"
    flatten = mc.get("flatten_policy") or "manual"
    product = mc.get("product_type") or "auto"

    return (
        f"**Orchestrator phase complete** — confirmed mandate for **{name}** (`{agent_id}`):\n"
        f"- Symbols: **{symbols}**\n"
        f"- Instruments: **{instruments}**\n"
        f"- Holding: **{holding}** | Flatten: **{flatten}** | Product: {product}\n\n"
        "This chat is now the agent session. Ignore pre-commit orchestrator proposals for other symbols."
    )


def promote_orchestrator_session(
    *,
    session_service: Any,
    orchestrator_session_id: str,
    agent_id: str,
    name: str,
    session_cfg: dict[str, Any],
    proposal: dict[str, Any] | None = None,
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
            "Bootstrap watch + research turns are starting — activity will appear below."
        ),
    )
    session_service.store.append_message(transition)
    session_service.event_bus.emit(
        orch_sid,
        "message.received",
        {"message_id": transition.message_id, "role": "system", "content": transition.content},
    )

    divider = Message(
        session_id=orch_sid,
        role="system",
        content=_mandate_divider_content(name=name, agent_id=agent_id, proposal=proposal),
    )
    session_service.store.append_message(divider)
    session_service.event_bus.emit(
        orch_sid,
        "message.received",
        {"message_id": divider.message_id, "role": "system", "content": divider.content},
    )

    session.config["history_cutoff_message_id"] = divider.message_id
    session_service.store.update_session(session)

    session_service.event_bus.emit(
        orch_sid,
        "session.promoted",
        {"session_id": orch_sid, "agent_id": agent_id, "session_kind": "autonomous_agent"},
    )
    return orch_sid
