"""Job id helpers for autonomous agents (no Vibe imports)."""

from __future__ import annotations

JOB_TYPE_WATCH = "autonomous_agent_watch"
JOB_TYPE_RESEARCH = "autonomous_agent_research"
JOB_TYPE_QUANT = "autonomous_agent_quant"
JOB_TYPE_INFRA_HEAL = "autonomous_agent_infra_heal"
AUTONOMOUS_JOB_TYPES = frozenset({JOB_TYPE_WATCH, JOB_TYPE_RESEARCH, JOB_TYPE_QUANT, JOB_TYPE_INFRA_HEAL})


def job_ids(agent_id: str) -> tuple[str, str, str]:
    return f"{agent_id}-watch", f"{agent_id}-research", f"{agent_id}-quant"
