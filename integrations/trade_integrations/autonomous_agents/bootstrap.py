"""Post-commit bootstrap — immediate watch tick + first reasoning turn."""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from trade_integrations.autonomous_agents.store import get_agent, save_agent
from trade_integrations.autonomous_agents.watch import dispatch_full_reasoning, run_watch_tick

logger = logging.getLogger(__name__)


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

    latest = get_agent(agent_id) or agent
    latest["bootstrap_status"] = "done"
    latest["bootstrap_completed_at"] = datetime.now(timezone.utc).isoformat()
    latest.pop("bootstrap_error", None)
    save_agent(latest)
