"""Shared bridge actions invoked from Nautilus actors and poll loop."""

from __future__ import annotations

from typing import Any

from nautilus_openalgo_bridge.agent_scoping import default_exit_underlying, strategy_tag_for_agent
from nautilus_openalgo_bridge.handoff import load_handoff
from nautilus_openalgo_bridge.models import ExecutionIntent, IntentAction, WatchAlert


def dispatch_exit_intent(
    agent_id: str,
    alert: WatchAlert,
    *,
    underlying: str | None = None,
) -> dict[str, Any]:
    from nautilus_openalgo_bridge.intent_queue import submit_intent

    handoff = load_handoff(agent_id)
    intent = ExecutionIntent(
        action=IntentAction.EXIT,
        agent_id=agent_id,
        rationale=alert.message,
        underlying=default_exit_underlying(agent_id, explicit=underlying),
        legs=[],
        strategy=strategy_tag_for_agent(agent_id),
    )
    path = submit_intent(intent)
    return {
        "status": "queued",
        "intent_id": intent.intent_id,
        "path": str(path),
    }
