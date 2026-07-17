"""Initial trade-plan approval gate for autonomous agents."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


def is_plan_approved(agent: dict[str, Any]) -> bool:
    """True once the user approved the bootstrap plan (agent fully autonomous)."""
    if agent.get("plan_approved_at"):
        return True
    status = str(agent.get("bootstrap_status") or "")
    return status == "done" and not agent.get("plan_approval_required")


def is_awaiting_plan_approval(agent: dict[str, Any]) -> bool:
    return str(agent.get("bootstrap_status") or "") == "awaiting_plan_approval"


def approve_agent_plan(agent_id: str) -> dict[str, Any]:
    from trade_integrations.autonomous_agents.store import get_agent, save_agent

    agent = get_agent(agent_id)
    if not agent:
        return {"status": "error", "error": f"agent not found: {agent_id}"}
    if is_plan_approved(agent):
        return {"status": "ok", "already_approved": True, "agent": agent}
    status = str(agent.get("bootstrap_status") or "")
    if status != "awaiting_plan_approval":
        return {"status": "error", "error": f"agent not awaiting plan approval (bootstrap_status={status})"}

    now = datetime.now(timezone.utc).isoformat()
    agent["plan_approved_at"] = now
    agent["bootstrap_status"] = "done"
    agent["bootstrap_completed_at"] = agent.get("bootstrap_completed_at") or now
    agent.pop("plan_approval_required", None)
    save_agent(agent)

    try:
        from trade_integrations.autonomous_agents.nautilus_watch import ensure_nautilus_watch_for_agent

        ensure_nautilus_watch_for_agent(agent_id)
    except Exception:
        pass

    watch_spec = dict(agent.get("watch_spec") or {})
    if watch_spec.get("rules"):
        try:
            from nautilus_openalgo_bridge.handoff import sync_watch_spec_to_handoff

            sync_watch_spec_to_handoff(agent_id, watch_spec)
        except Exception:
            pass

    return {"status": "ok", "agent": agent, "plan_approved_at": now}


def reject_agent_plan(agent_id: str, *, note: str = "") -> dict[str, Any]:
    from trade_integrations.autonomous_agents.store import get_agent, save_agent

    agent = get_agent(agent_id)
    if not agent:
        return {"status": "error", "error": f"agent not found: {agent_id}"}
    agent["bootstrap_status"] = "plan_rejected"
    if note.strip():
        guidance = list(agent.get("user_guidance") or [])
        guidance.append({"at": datetime.now(timezone.utc).isoformat(), "text": note.strip()})
        agent["user_guidance"] = guidance[-10:]
    save_agent(agent)
    return {"status": "ok", "agent": agent}
