"""Shared bridge actions invoked from Nautilus actors and poll loop."""

from __future__ import annotations

from typing import Any

from nautilus_openalgo_bridge.handoff import load_handoff
from nautilus_openalgo_bridge.models import ExecutionIntent, IntentAction, WatchAlert


def dispatch_exit_intent(
    agent_id: str,
    alert: WatchAlert,
    *,
    underlying: str | None = None,
) -> dict[str, Any]:
    from nautilus_openalgo_bridge.execute import execute_intent

    handoff = load_handoff(agent_id)
    intent = ExecutionIntent(
        action=IntentAction.EXIT,
        agent_id=agent_id,
        rationale=alert.message,
        underlying=underlying or (handoff.underlying if handoff else "NIFTY"),
        legs=list(handoff.legs) if handoff and handoff.legs else [],
        strategy="nautilus_stop",
    )
    return execute_intent(intent)
