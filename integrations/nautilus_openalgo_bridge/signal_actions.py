"""Shared bridge actions invoked from Nautilus actors and poll loop."""

from __future__ import annotations

from typing import Any

from nautilus_openalgo_bridge.agent_scoping import strategy_tag_for_agent
from nautilus_openalgo_bridge.handoff import load_handoff
from nautilus_openalgo_bridge.models import ExecutionIntent, IntentAction, WatchAlert


def dispatch_exit_intent(
    agent_id: str,
    alert: WatchAlert,
    *,
    underlying: str | None = None,
) -> dict[str, Any]:
    try:
        from trade_integrations.autonomous_agents.store import get_agent
        from trade_integrations.execution.profile import resolve_profile

        agent = get_agent(agent_id) or {}
        profile = resolve_profile(agent=agent)
    except Exception:
        profile = None

    if profile is not None and profile.is_us:
        from nautilus_openalgo_bridge.vibe_trigger import dispatch_us_exit_alert_sync

        return dispatch_us_exit_alert_sync(agent_id, alert)

    from nautilus_openalgo_bridge.intent_queue import submit_intent

    handoff = load_handoff(agent_id)
    intent = ExecutionIntent(
        action=IntentAction.EXIT,
        agent_id=agent_id,
        rationale=alert.message,
        underlying=underlying or (handoff.underlying if handoff else "NIFTY"),
        legs=[],
        strategy=strategy_tag_for_agent(agent_id),
    )
    path = submit_intent(intent)
    return {
        "status": "queued",
        "intent_id": intent.intent_id,
        "path": str(path),
    }
