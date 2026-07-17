"""Post-commit bootstrap — immediate watch tick + first reasoning turn."""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from trade_integrations.autonomous_agents.store import get_agent, save_agent
from trade_integrations.autonomous_agents.watch import dispatch_full_reasoning, run_watch_tick

logger = logging.getLogger(__name__)


def finalize_bootstrap_if_ready(agent_id: str) -> bool:
    """Move to plan approval once bootstrap decision + watch_spec are recorded."""
    agent = get_agent(agent_id)
    if not agent or str(agent.get("bootstrap_status")) != "running":
        return False
    if not agent.get("last_decision"):
        return False

    now = datetime.now(timezone.utc).isoformat()
    agent["bootstrap_status"] = "awaiting_plan_approval"
    agent["plan_approval_required"] = True
    agent["bootstrap_completed_at"] = now
    agent.pop("bootstrap_error", None)
    save_agent(agent)
    logger.info("agent %s awaiting plan approval", agent_id)
    try:
        import sys

        host = sys.modules.get("api_server") or sys.modules.get("agent.api_server")
        svc = host._get_session_service() if host else None
        session_id = str(agent.get("vibe_session_id") or "")
        if svc and session_id:
            svc.event_bus.emit(
                session_id,
                "autonomous_agent.plan_ready",
                {
                    "agent_id": agent_id,
                    "bootstrap_status": "awaiting_plan_approval",
                    "strategy": (agent.get("thesis") or {}).get("strategy"),
                },
            )
    except Exception as exc:
        logger.debug("plan_ready emit failed for %s: %s", agent_id, exc)
    return True


async def bootstrap_agent(agent_id: str) -> None:
    """Run first watch tick and bootstrap research turn for a newly committed agent."""
    agent = get_agent(agent_id)
    if not agent or str(agent.get("status")) != "running":
        return

    agent["bootstrap_status"] = "running"
    agent.pop("bootstrap_error", None)
    save_agent(agent)

    try:
        await run_watch_tick(agent_id)
        dispatched = await dispatch_full_reasoning(agent_id, turn_kind="bootstrap")
        if not dispatched:
            raise RuntimeError("bootstrap research turn was not dispatched (session unavailable or turn in flight)")
    except Exception as exc:
        logger.warning("bootstrap failed for %s: %s", agent_id, exc, exc_info=True)
        latest = get_agent(agent_id) or agent
        latest["bootstrap_status"] = "failed"
        latest["bootstrap_error"] = str(exc)[:500]
        latest["bootstrap_completed_at"] = datetime.now(timezone.utc).isoformat()
        save_agent(latest)
        return

    # bootstrap_status stays "running" until record_autonomous_decision + finalize
