"""Runtime observability for autonomous agent instances."""

from __future__ import annotations

import os
import time
from datetime import datetime, timezone
from typing import Any


def _nautilus_watch_enabled() -> bool:
    try:
        from nautilus_openalgo_bridge.config import is_watch_enabled

        return is_watch_enabled()
    except ImportError:
        raw = os.getenv("NAUTILUS_WATCH_ENABLE", "true").strip().lower()
        return raw not in {"0", "false", "no", "off"}


def _nautilus_process_alive() -> bool:
    try:
        from trade_integrations.autonomous_agents.nautilus_watch import get_watch_process_status

        return bool(get_watch_process_status().get("alive"))
    except ImportError:
        pass
    from pathlib import Path

    candidates: list[Path] = []
    try:
        from trade_integrations.context.hub import get_hub_dir

        trade_root = get_hub_dir().parent.parent
        candidates.append(trade_root / "log" / "nautilus-watch.pid")
    except Exception:
        pass
    candidates.append(Path.home() / ".vibe-trading" / "logs" / "nautilus-watch.pid")

    for candidate in candidates:
        if not candidate.is_file():
            continue
        try:
            pid = int(candidate.read_text(encoding="utf-8").strip())
            os.kill(pid, 0)
            return True
        except (OSError, ValueError):
            continue
    return False


def _parse_iso_age_min(iso: str | None) -> float | None:
    if not iso:
        return None
    try:
        dt = datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
        return (datetime.now(timezone.utc) - dt).total_seconds() / 60.0
    except ValueError:
        return None


def _handoff_details(agent_id: str) -> tuple[bool, bool]:
    """Return (watch_configured, position_tracked)."""
    try:
        from nautilus_openalgo_bridge.handoff import load_handoff

        handoff = load_handoff(agent_id)
        if handoff is None:
            return False, False
        return True, bool(handoff.legs)
    except ImportError:
        return False, False


def _autonomous_job_health(agent_id: str) -> str | None:
    try:
        from src.scheduled_research.models import JobStatus
        from src.scheduled_research.store import ScheduledResearchJobStore

        store = ScheduledResearchJobStore()
        watch_id = f"{agent_id}-watch"
        research_id = f"{agent_id}-research"
        jobs = [store.get(watch_id), store.get(research_id)]
        if not any(jobs):
            return None
        now_ms = int(time.time() * 1000)
        any_ok = False
        for job in jobs:
            if job is None:
                continue
            if job.status == JobStatus.FAILED:
                return "stale"
            if job.last_run_at is not None:
                age_min = (now_ms - job.last_run_at) / 60_000
                stale_after = max(
                    15.0,
                    int(job.schedule) / 60_000 * 2 if str(job.schedule).isdigit() else 15.0,
                )
                if age_min <= stale_after:
                    any_ok = True
        return "ok" if any_ok else "stale"
    except Exception:
        return None


def _scheduler_health_for_agent(
    agent: dict[str, Any],
    *,
    linked_paper: dict[str, Any],
    profile: Any | None = None,
) -> str:
    bootstrap = str(agent.get("bootstrap_status") or "")
    if bootstrap == "failed":
        return "bootstrap_failed"
    if bootstrap in {"pending", "running"}:
        return "initializing"

    created_age = _parse_iso_age_min(agent.get("created_at"))
    if (
        created_age is not None
        and created_age <= 5.0
        and not agent.get("last_watch_at")
        and not agent.get("last_full_reasoning_at")
        and bootstrap != "done"
    ):
        return "initializing"

    try:
        if profile is None:
            from trade_integrations.execution.profile import resolve_profile

            profile = resolve_profile(agent=agent)
    except Exception:
        profile = None

    if profile is not None and profile.uses_nautilus_handoff:
        job_health = _autonomous_job_health(str(agent.get("id") or ""))
        schedules = dict(agent.get("schedules") or {})
        watch_ms = int(schedules.get("watch_ms") or 420_000)
        research_ms = int(schedules.get("research_ms") or 5_400_000)
        stale_watch_min = max(10.0, (watch_ms / 60_000) * 2)
        stale_research_min = max(15.0, (research_ms / 60_000) * 1.5)

        watch_age = _parse_iso_age_min(agent.get("last_watch_at"))
        reason_age = _parse_iso_age_min(
            agent.get("last_full_reasoning_at") or agent.get("last_revision_at")
        )

        if job_health == "ok":
            return "ok"
        if watch_age is not None and watch_age <= stale_watch_min:
            return "ok"
        if reason_age is not None and reason_age <= stale_research_min:
            return "ok"
        return "stale"

    if not linked_paper.get("enabled"):
        return "disabled"
    last = linked_paper.get("last_agent_turn_at")
    if not last:
        return "stale"
    try:
        from trade_integrations.autonomous_agents.trading_config import get_agent_trading_config

        cfg = get_agent_trading_config()
        age_min = _parse_iso_age_min(str(last))
        if age_min is None:
            return "stale"
        stale_after = max(10.0, (cfg.poll_interval_ms or 300_000) / 60_000 * 2)
        return "ok" if age_min <= stale_after else "stale"
    except ValueError:
        return "stale"


def _nautilus_state_for_agent(agent: dict[str, Any]) -> str:
    if not _nautilus_watch_enabled():
        return "off"
    if _nautilus_process_alive():
        return "node_on"

    schedules = dict(agent.get("schedules") or {})
    watch_ms = int(schedules.get("watch_ms") or 420_000)
    stale_watch_min = max(15.0, (watch_ms / 60_000) * 2)
    watch_age = _parse_iso_age_min(agent.get("last_watch_at"))
    if watch_age is not None and watch_age <= stale_watch_min:
        return "poll_ok"

    bootstrap = str(agent.get("bootstrap_status") or "")
    created_age = _parse_iso_age_min(agent.get("created_at"))
    if bootstrap in {"pending", "running"}:
        return "expected"
    if created_age is not None and created_age <= 5.0 and bootstrap != "done":
        return "expected"

    if watch_age is not None and watch_age > stale_watch_min:
        return "stale"
    if created_age is not None and created_age > 10.0 and watch_age is None:
        return "stale"
    return "expected"


def _paper_runtime(agent: dict[str, Any] | None = None, *, authority: Any | None = None) -> dict[str, Any]:
    try:
        from trade_integrations.autonomous_agents.agent_status import get_agent_execution_status, load_openalgo_authority

        agent_id = str((agent or {}).get("id") or "").strip() or None
        resolved_authority = authority if authority is not None else load_openalgo_authority(agent=agent)
        return get_agent_execution_status(agent_id=agent_id, agent=agent, authority=resolved_authority)
    except Exception as exc:
        return {"error": str(exc)}


def _resolve_watch_path_for_agent(
    *,
    agent_id: str,
    profile: Any | None,
    nautilus_on: bool,
    nautilus_alive: bool,
    in_registry: bool,
    nautilus_bound_agent: str | None,
) -> str:
    if profile is not None and profile.uses_nautilus_watch:
        if not nautilus_on:
            return "degraded"
        bound = str(nautilus_bound_agent or "").strip()
        if nautilus_alive and (in_registry or (bound and bound == agent_id)):
            return "nautilus_detached"
        if nautilus_alive:
            return "nautilus_scheduler_poll"
        return "degraded"
    return "agent_native"


def build_agent_runtime(agent: dict[str, Any], *, authority: Any | None = None) -> dict[str, Any]:
    """Trader brain state — distinct from HTTP infra health."""
    if str(agent.get("status") or "") == "draft":
        return {
            "status": "draft",
            "scheduler_health": "disabled",
            "nautilus_watch_enabled": False,
            "nautilus_state": "off",
            "watch_path": "draft",
        }

    agent_id = str(agent.get("id") or "")
    mc = dict(agent.get("mandate_config") or {})
    alert_rules = dict(agent.get("alert_rules") or mc.get("alert_rules") or {})

    from trade_integrations.autonomous_agents.agent_status import (
        get_agent_execution_status,
        load_openalgo_authority,
    )

    authority = authority if authority is not None else load_openalgo_authority(agent=agent)
    if authority.market_context is not None:
        from trade_integrations.execution.profile import resolve_profile_from_context

        try:
            profile = resolve_profile_from_context(agent=agent, market_context=authority.market_context)
        except Exception:
            profile = None
    else:
        from trade_integrations.execution.profile import resolve_profile

        try:
            profile = resolve_profile(agent=agent)
        except Exception:
            profile = None

    paper = get_agent_execution_status(agent=agent, agent_id=agent_id or None, authority=authority)
    session = dict(paper.get("session") or {})
    linked = str(session.get("autonomous_agent_id") or "") == agent_id

    last_decision = agent.get("last_decision") or session.get("last_decision") if linked else agent.get("last_decision")

    nautilus_on = _nautilus_watch_enabled()
    nautilus_alive = _nautilus_process_alive()
    nautilus_state = _nautilus_state_for_agent(agent)
    nautilus_bound_agent: str | None = None
    registry_agent_ids: list[str] = []
    in_registry = False
    try:
        from trade_integrations.autonomous_agents.nautilus_watch import (
            get_watch_process_status,
            is_agent_in_registry,
        )

        status = get_watch_process_status()
        nautilus_bound_agent = status.get("bound_agent_id")  # type: ignore[assignment]
        registry_agent_ids = list(status.get("registry_agent_ids") or [])
        in_registry = is_agent_in_registry(agent_id)
    except Exception:
        pass
    watch_configured, position_tracked = _handoff_details(agent_id) if agent_id else (False, False)

    watch_path = _resolve_watch_path_for_agent(
        agent_id=agent_id,
        profile=profile,
        nautilus_on=nautilus_on,
        nautilus_alive=nautilus_alive,
        in_registry=in_registry,
        nautilus_bound_agent=nautilus_bound_agent,
    )

    scheduler_health = _scheduler_health_for_agent(
        agent,
        linked_paper=session if linked else {},
        profile=profile,
    )

    return {
        "mandate_summary": {
            "holding_period": mc.get("holding_period"),
            "flatten_policy": mc.get("flatten_policy"),
            "product_type": mc.get("product_type"),
            "revision_policy": mc.get("revision_policy"),
            "confidence_threshold": mc.get("confidence_threshold")
            or (agent.get("constraints") or {}).get("confidence_threshold"),
            "allowed_instruments": mc.get("allowed_instruments"),
        },
        "alert_rules_summary": {
            "spot_move_pct": alert_rules.get("spot_move_pct"),
            "vix_above": alert_rules.get("vix_above"),
            "thesis_break": alert_rules.get("thesis_break", True),
        },
        "bootstrap_status": agent.get("bootstrap_status"),
        "bootstrap_error": agent.get("bootstrap_error"),
        "scheduler_health": scheduler_health,
        "market_open": paper.get("market_open"),
        "nautilus_watch_enabled": nautilus_on,
        "nautilus_process_alive": nautilus_alive,
        "nautilus_state": nautilus_state,
        "nautilus_bound_agent_id": nautilus_bound_agent,
        "nautilus_registry_agent_ids": registry_agent_ids,
        "nautilus_in_registry": in_registry,
        "watch_path": watch_path,
        "watch_configured": watch_configured,
        "position_tracked": position_tracked,
        "handoff_active": watch_configured or position_tracked,
        "paper_session_linked": linked,
        "last_decision": last_decision,
        "last_watch_summary": agent.get("last_watch_summary"),
        "last_revision_at": agent.get("last_revision_at"),
        "last_bridge_alert_at": agent.get("last_bridge_alert_at"),
        "open_positions": paper.get("open_positions") if linked else None,
        "execution_context": paper.get("execution_context"),
        "analyze_mode": paper.get("analyze_mode"),
        "watch_strategy": (agent.get("watch_spec") or {}).get("strategy") or (agent.get("thesis") or {}).get("strategy"),
        "watch_spec_updated_at": agent.get("watch_spec_updated_at"),
    }


def build_stack_health(*, authority: Any | None = None) -> dict[str, Any]:
    """Infra vs trader summary for hub header."""
    from trade_integrations.autonomous_agents.agent_status import load_openalgo_authority
    from trade_integrations.autonomous_agents.store import list_agents

    shared_authority = authority if authority is not None else load_openalgo_authority(agent=None)
    running = [a for a in list_agents() if str(a.get("status") or "") == "running"]
    anchor = running[0] if running else None
    paper = _paper_runtime(anchor, authority=shared_authority)
    session = dict(paper.get("session") or {})
    agent_id = str(session.get("autonomous_agent_id") or "").strip()

    scheduler_health = paper.get("scheduler_health")
    stack_profile = None
    if agent_id:
        try:
            from trade_integrations.autonomous_agents.store import get_agent
            from trade_integrations.execution.profile import resolve_profile, resolve_profile_from_context

            agent = get_agent(agent_id)
            if agent:
                if shared_authority.market_context is not None:
                    stack_profile = resolve_profile_from_context(
                        agent=agent,
                        market_context=shared_authority.market_context,
                    )
                else:
                    stack_profile = resolve_profile(agent=agent)
                scheduler_health = _scheduler_health_for_agent(
                    agent,
                    linked_paper=session,
                    profile=stack_profile,
                )
        except Exception:
            pass

    nautilus_state = "off"
    nautilus_bound_agent: str | None = None
    registry_agent_ids: list[str] = []
    if _nautilus_watch_enabled():
        nautilus_state = "node_on" if _nautilus_process_alive() else "expected"
        try:
            from trade_integrations.autonomous_agents.nautilus_watch import get_watch_process_status

            status = get_watch_process_status()
            nautilus_bound_agent = status.get("bound_agent_id")  # type: ignore[assignment]
            registry_agent_ids = list(status.get("registry_agent_ids") or [])
        except Exception:
            pass

    return {
        "nautilus_watch_enabled": _nautilus_watch_enabled(),
        "nautilus_process_alive": _nautilus_process_alive(),
        "nautilus_state": nautilus_state,
        "nautilus_bound_agent_id": nautilus_bound_agent,
        "nautilus_registry_agent_ids": registry_agent_ids,
        "scheduler_health": scheduler_health,
        "market_open": paper.get("market_open"),
        "paper_session_enabled": bool(session.get("enabled")),
    }


def enrich_agent(agent: dict[str, Any], *, authority: Any | None = None) -> dict[str, Any]:
    out = dict(agent)
    out["runtime"] = build_agent_runtime(agent, authority=authority)
    return out
