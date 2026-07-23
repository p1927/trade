"""Initial trade-plan approval gate for autonomous agents."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)


def is_plan_approved(agent: dict[str, Any]) -> bool:
    """True once the user approved the bootstrap plan (agent fully autonomous)."""
    return bool(agent.get("plan_approved_at"))


def is_awaiting_plan_approval(agent: dict[str, Any]) -> bool:
    return str(agent.get("bootstrap_status") or "") == "awaiting_plan_approval"


def resolve_widget_id(agent: dict[str, Any]) -> str:
    """Best-effort widget id for plan approval UI."""
    for key in (
        "active_trade_plan_widget_id",
        "approved_trade_plan_widget_id",
    ):
        wid = str(agent.get(key) or "").strip()
        if wid:
            return wid
    last = dict(agent.get("last_decision") or {})
    wid = str(last.get("widget_id") or "").strip()
    if wid:
        return wid
    thesis = dict(agent.get("thesis") or {})
    return str(thesis.get("widget_id") or "").strip()


def _infer_revision_source(agent: dict[str, Any]) -> str:
    turn = str(agent.get("active_turn_kind") or "").strip()
    if turn == "strategy_revision":
        return "watcher"
    status = str(agent.get("bootstrap_status") or "")
    if status in {"pending", "running", "awaiting_plan_approval"}:
        return "bootstrap"
    if turn in {"user_chat", "user_guidance"}:
        return "user_guidance"
    return "informational"


def activate_agent_watch(agent_id: str, agent: dict[str, Any]) -> None:
    """Start Nautilus watch and sync handoff watch_spec after plan approval."""
    try:
        from trade_integrations.autonomous_agents.nautilus_watch import ensure_nautilus_watch_for_agent

        ensure_nautilus_watch_for_agent(agent_id)
    except Exception:
        logger.debug("ensure_nautilus_watch_for_agent failed for %s", agent_id, exc_info=True)

    watch_spec = dict(agent.get("watch_spec") or {})
    if watch_spec.get("rules"):
        try:
            from nautilus_openalgo_bridge.handoff import sync_watch_spec_to_handoff

            sync_watch_spec_to_handoff(agent_id, watch_spec)
        except Exception:
            logger.debug("sync_watch_spec_to_handoff failed for %s", agent_id, exc_info=True)


def activate_agent_watch_after_approval(agent_id: str, agent: dict[str, Any]) -> None:
    """Backward-compatible alias."""
    activate_agent_watch(agent_id, agent)


def _activate_deferred_watch_spec(agent_id: str, agent: dict[str, Any]) -> None:
    from trade_integrations.autonomous_agents.mcp_actions import activate_watch_spec_for_agent
    from trade_integrations.execution.profile import resolve_profile

    watch_spec = dict(agent.get("watch_spec") or {})
    if not watch_spec.get("rules"):
        return
    profile = resolve_profile(agent=agent)
    activate_watch_spec_for_agent(agent_id, agent, watch_spec, profile=profile)


def _pause_scheduled_research(agent_id: str) -> None:
    try:
        from src.scheduled_research.store import ScheduledResearchJobStore

        store = ScheduledResearchJobStore()
        job = store.get(f"{agent_id}-research")
        if job is None:
            return
        now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        agent = None
        try:
            from trade_integrations.autonomous_agents.store import get_agent

            agent = get_agent(agent_id) or {}
        except Exception:
            agent = {}
        research_ms = int((agent.get("schedules") or {}).get("research_ms") or 5_400_000)
        target = now_ms + research_ms
        if job.next_run_at is None or job.next_run_at < target:
            job.next_run_at = target
            store.upsert(job)
    except Exception:
        logger.debug("pause scheduled research failed for %s", agent_id, exc_info=True)


def on_trade_plan_widget_emitted(
    agent_id: str,
    widget_id: str,
    *,
    revision_source: str | None = None,
) -> None:
    """Persist widget id and drive bootstrap finalize / re-approval."""
    from trade_integrations.autonomous_agents.store import get_agent, save_agent

    wid = str(widget_id or "").strip()
    if not wid:
        return
    agent = get_agent(agent_id)
    if not agent:
        return

    source = str(revision_source or _infer_revision_source(agent) or "").strip() or "informational"

    if source == "watcher":
        agent["plan_revision_source"] = "watcher"
        save_agent(agent)
        return

    if is_plan_approved(agent):
        if source == "user_guidance":
            request_plan_reapproval(agent_id, wid, source="user_guidance")
        return

    status = str(agent.get("bootstrap_status") or "")
    if status in {"pending", "running", "awaiting_plan_approval"}:
        agent["active_trade_plan_widget_id"] = wid
        agent["plan_revision_source"] = "bootstrap"
        save_agent(agent)
        if status == "running":
            from trade_integrations.autonomous_agents.bootstrap import safe_finalize_bootstrap_if_ready

            safe_finalize_bootstrap_if_ready(agent_id)


def request_plan_reapproval(agent_id: str, widget_id: str, *, source: str = "user_guidance") -> dict[str, Any]:
    from trade_integrations.autonomous_agents.store import get_agent, save_agent

    agent = get_agent(agent_id)
    if not agent:
        return {"status": "error", "error": f"agent not found: {agent_id}"}
    if not is_plan_approved(agent):
        return {"status": "skipped", "reason": "plan_not_yet_approved"}

    wid = str(widget_id or "").strip()
    if not wid:
        return {"status": "error", "error": "widget_id required"}

    agent["bootstrap_status"] = "awaiting_plan_approval"
    agent["plan_approval_required"] = True
    agent.pop("plan_approved_at", None)
    agent["active_trade_plan_widget_id"] = wid
    agent["plan_revision_source"] = source
    save_agent(agent)
    _pause_scheduled_research(agent_id)
    return {"status": "ok", "agent": agent}


def approve_agent_plan(agent_id: str, *, widget_id: str | None = None) -> dict[str, Any]:
    from trade_integrations.autonomous_agents.store import get_agent, save_agent

    agent = get_agent(agent_id)
    if not agent:
        return {"status": "error", "error": f"agent not found: {agent_id}"}
    if is_plan_approved(agent):
        return {"status": "ok", "already_approved": True, "agent": agent}
    status = str(agent.get("bootstrap_status") or "")
    if status != "awaiting_plan_approval":
        return {"status": "error", "error": f"agent not awaiting plan approval (bootstrap_status={status})"}

    active = str(agent.get("active_trade_plan_widget_id") or resolve_widget_id(agent) or "").strip()
    if widget_id:
        expected = str(widget_id).strip()
        if active and expected != active:
            return {
                "status": "error",
                "error": f"widget_id mismatch (active={active}, requested={expected})",
            }
        active = expected or active

    now = datetime.now(timezone.utc).isoformat()
    agent["plan_approved_at"] = now
    agent["bootstrap_status"] = "done"
    agent["bootstrap_completed_at"] = agent.get("bootstrap_completed_at") or now
    agent.pop("plan_approval_required", None)
    if active:
        agent["active_trade_plan_widget_id"] = active
        agent["approved_trade_plan_widget_id"] = active
    save_agent(agent)

    activate_agent_watch(agent_id, agent)
    _activate_deferred_watch_spec(agent_id, agent)

    return {"status": "ok", "agent": agent, "plan_approved_at": now}


def reject_agent_plan(agent_id: str, *, note: str = "") -> dict[str, Any]:
    from trade_integrations.autonomous_agents.store import get_agent, save_agent

    agent = get_agent(agent_id)
    if not agent:
        return {"status": "error", "error": f"agent not found: {agent_id}"}
    agent["bootstrap_status"] = "plan_rejected"
    agent["plan_approval_required"] = False
    if note.strip():
        guidance = list(agent.get("user_guidance") or [])
        guidance.append({"at": datetime.now(timezone.utc).isoformat(), "text": note.strip()})
        agent["user_guidance"] = guidance[-10:]
    save_agent(agent)
    return {"status": "ok", "agent": agent}


def assert_plan_approved(agent: dict[str, Any]) -> None:
    from trade_integrations.autonomous_agents.mandate_enforcer import MandateViolation

    if not is_plan_approved(agent):
        raise MandateViolation(
            "plan_not_approved",
            "Trade plan must be user-approved before execution",
        )


def normalize_legacy_plan_approval(agent: dict[str, Any]) -> tuple[dict[str, Any], bool]:
    """Backfill plan approval fields for agents created before the widget-first gate."""
    if str(agent.get("status") or "") == "draft":
        return agent, False

    updated = dict(agent)
    changed = False
    wid = resolve_widget_id(updated)
    status = str(updated.get("bootstrap_status") or "")

    if status == "done" and updated.get("plan_approved_at") and wid:
        if not updated.get("approved_trade_plan_widget_id"):
            updated["approved_trade_plan_widget_id"] = wid
            changed = True
        if not updated.get("active_trade_plan_widget_id"):
            updated["active_trade_plan_widget_id"] = wid
            changed = True

    if (
        status == "done"
        and updated.get("last_decision")
        and updated.get("watch_spec")
        and not updated.get("plan_approved_at")
    ):
        updated["plan_approved_at"] = (
            updated.get("bootstrap_completed_at") or updated.get("updated_at") or datetime.now(timezone.utc).isoformat()
        )
        changed = True
        if wid:
            if not updated.get("approved_trade_plan_widget_id"):
                updated["approved_trade_plan_widget_id"] = wid
                changed = True
            if not updated.get("active_trade_plan_widget_id"):
                updated["active_trade_plan_widget_id"] = wid
                changed = True

    return updated, changed


def ensure_plan_approval_record(agent: dict[str, Any], *, persist: bool = False) -> dict[str, Any]:
    """Apply lazy legacy backfill when loading an agent from hub storage."""
    normalized, changed = normalize_legacy_plan_approval(agent)
    if not changed:
        return agent
    if persist:
        from trade_integrations.autonomous_agents.store import save_agent

        save_agent(normalized)
        logger.info("backfilled plan approval fields for agent %s", normalized.get("id"))
    return normalized
