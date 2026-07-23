"""Initial trade-plan approval gate for autonomous agents."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)


def is_plan_approved(agent: dict[str, Any]) -> bool:
    """True once the user approved the bootstrap plan (agent fully autonomous)."""
    from trade_integrations.autonomous_agents.mandate_config import is_observe_agent

    if is_observe_agent(agent) and str(agent.get("bootstrap_status") or "") == "done":
        return True
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


def _normalize_agent_watch_spec(agent: dict[str, Any]) -> dict[str, Any] | None:
    """Resolve watch_spec with coerced rules; build from thesis strategy when missing."""
    from trade_integrations.autonomous_agents.bootstrap import _coerce_watch_rules

    for raw in (
        dict(agent.get("watch_spec") or {}),
        dict((agent.get("mandate_config") or {}).get("watch_spec") or {}),
    ):
        rules = _coerce_watch_rules(raw.get("rules"))
        if rules:
            spec = dict(raw)
            spec["rules"] = rules
            if not spec.get("strategy"):
                thesis_strategy = (agent.get("thesis") or {}).get("strategy")
                if thesis_strategy:
                    spec["strategy"] = thesis_strategy
            return spec

    strategy = (agent.get("thesis") or {}).get("strategy")
    if strategy:
        from trade_integrations.autonomous_agents.mandate import mandate_config_from_agent
        from trade_integrations.autonomous_agents.strategy_watch_spec import build_watch_spec_for_strategy

        mc = mandate_config_from_agent(agent)
        symbols = list(agent.get("symbols") or ["NIFTY"])
        built = build_watch_spec_for_strategy(
            strategy=str(strategy),
            mandate=mc,
            symbols=symbols,
        )
        rules = _coerce_watch_rules(built.get("rules"))
        if rules:
            built["rules"] = rules
            return built
    return None


def activate_agent_watch(agent_id: str, agent: dict[str, Any]) -> None:
    """Start Nautilus watch and sync handoff watch_spec after plan approval."""
    from trade_integrations.autonomous_agents.store import get_agent, save_agent

    try:
        from trade_integrations.autonomous_agents.nautilus_watch import ensure_nautilus_watch_for_agent

        ensure_nautilus_watch_for_agent(agent_id)
    except Exception:
        logger.warning("ensure_nautilus_watch_for_agent failed for %s", agent_id, exc_info=True)

    watch_spec = _normalize_agent_watch_spec(agent) or {}
    if watch_spec:
        agent = get_agent(agent_id) or agent
        agent["watch_spec"] = watch_spec
        mc = dict(agent.get("mandate_config") or {})
        mc["watch_spec"] = watch_spec
        agent["mandate_config"] = mc
        save_agent(agent)

    watch_spec = dict(agent.get("watch_spec") or {})
    if watch_spec.get("rules"):
        try:
            from nautilus_openalgo_bridge.handoff import sync_watch_spec_to_handoff

            sync_watch_spec_to_handoff(agent_id, watch_spec)
        except Exception:
            logger.warning("sync_watch_spec_to_handoff failed for %s", agent_id, exc_info=True)


def _activate_deferred_watch_spec(agent_id: str, agent: dict[str, Any]) -> None:
    from trade_integrations.autonomous_agents.mcp_actions import activate_watch_spec_for_agent
    from trade_integrations.autonomous_agents.store import get_agent, save_agent
    from trade_integrations.execution.profile import resolve_profile

    agent = get_agent(agent_id) or agent
    watch_spec = _normalize_agent_watch_spec(agent)
    if not watch_spec:
        return
    agent["watch_spec"] = watch_spec
    mc = dict(agent.get("mandate_config") or {})
    mc["watch_spec"] = watch_spec
    agent["mandate_config"] = mc
    save_agent(agent)

    profile = resolve_profile(agent=agent)
    activate_watch_spec_for_agent(agent_id, agent, watch_spec, profile=profile)
    try:
        from trade_integrations.watch_registry.store import migrate_agent_watch_spec_to_registry

        migrate_agent_watch_spec_to_registry(agent_id)
    except Exception:
        logger.warning("migrate_agent_watch_spec_to_registry failed for %s", agent_id, exc_info=True)
    try:
        from trade_integrations.autonomous_agents.nautilus_watch import add_agent_to_registry

        add_agent_to_registry(agent_id)
    except Exception:
        logger.warning("add_agent_to_registry failed for %s", agent_id, exc_info=True)


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

    _activate_deferred_watch_spec(agent_id, agent)
    activate_agent_watch(agent_id, agent)
    try:
        from trade_integrations.watch_registry.store import sync_nautilus_registry_from_watches

        sync_nautilus_registry_from_watches(restart_if_changed=True)
    except Exception:
        logger.warning("nautilus registry sync failed after plan approval for %s", agent_id, exc_info=True)
    _nudge_watch_job(agent_id)

    watch_warnings = _collect_watch_activation_warnings(agent_id)
    result: dict[str, Any] = {"status": "ok", "agent": agent, "plan_approved_at": now}
    if watch_warnings:
        result["watch_activation_warnings"] = watch_warnings
    return result


def _collect_watch_activation_warnings(agent_id: str) -> list[str]:
    """Post-approval verification — surface registry/Nautilus gaps on the API response."""
    warnings: list[str] = []
    from trade_integrations.watch_registry.store import OWNER_KIND_AUTONOMOUS, list_watches

    if not list_watches(owner_kind=OWNER_KIND_AUTONOMOUS, owner_id=agent_id, active_only=True):
        warnings.append(f"No active registry watch for {agent_id} after plan approval")
    try:
        from trade_integrations.autonomous_agents.nautilus_watch import ensure_nautilus_watch_for_agent

        msg = ensure_nautilus_watch_for_agent(agent_id)
        if msg:
            warnings.append(str(msg))
    except Exception as exc:
        warnings.append(f"Nautilus watch ensure failed: {exc}")
    return warnings


def _nudge_watch_job(agent_id: str) -> None:
    try:
        import sys
        from pathlib import Path

        agent_src = Path(__file__).resolve().parents[3] / "vibetrading" / "agent"
        if agent_src.is_dir() and str(agent_src) not in sys.path:
            sys.path.insert(0, str(agent_src))
        from src.scheduled_research.autonomous_agent_jobs import nudge_watch_job_after_plan_approval

        nudge_watch_job_after_plan_approval(agent_id)
    except Exception:
        logger.debug("nudge watch job failed for %s", agent_id, exc_info=True)


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
