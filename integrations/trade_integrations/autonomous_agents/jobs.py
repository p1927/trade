"""Job id helpers for autonomous agents (no Vibe imports)."""

from __future__ import annotations

JOB_TYPE_WATCH = "autonomous_agent_watch"
JOB_TYPE_RESEARCH = "autonomous_agent_research"
AUTONOMOUS_JOB_TYPES = frozenset({JOB_TYPE_WATCH, JOB_TYPE_RESEARCH})


def job_ids(agent_id: str) -> tuple[str, str]:
    return f"{agent_id}-watch", f"{agent_id}-research"
