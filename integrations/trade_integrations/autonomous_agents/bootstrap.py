"""Post-commit bootstrap — immediate watch tick + first reasoning turn."""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from trade_integrations.autonomous_agents.store import get_agent, save_agent
from trade_integrations.autonomous_agents.watch import dispatch_full_reasoning, run_watch_tick

logger = logging.getLogger(__name__)


def finalize_bootstrap_if_ready(agent_id: str) -> bool:
    """Mark bootstrap done once the first decision is recorded."""
    agent = get_agent(agent_id)
    if not agent or str(agent.get("bootstrap_status")) != "running":
        return False
    if not agent.get("last_decision"):
        return False

    agent["bootstrap_status"] = "done"
    agent["bootstrap_completed_at"] = datetime.now(timezone.utc).isoformat()
    agent.pop("bootstrap_error", None)
    save_agent(agent)

    try:
        import sys
        from pathlib import Path

        agent_src = Path(__file__).resolve().parents[3] / "vibetrading" / "agent"
        if agent_src.is_dir() and str(agent_src) not in sys.path:
            sys.path.insert(0, str(agent_src))
        from src.scheduled_research.autonomous_agent_jobs import schedule_first_research_after_bootstrap

        schedule_first_research_after_bootstrap(agent_id)
    except Exception as exc:
        logger.debug("schedule first research after bootstrap failed for %s: %s", agent_id, exc)
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

    # bootstrap_status stays "running" until record_autonomous_decision + session finalize
