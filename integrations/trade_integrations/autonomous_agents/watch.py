"""Lightweight watch ticks and alert detection."""

from __future__ import annotations

import logging
import sys
from datetime import datetime, timezone
from typing import Any

from trade_integrations.autonomous_agents.store import get_agent, save_agent
from trade_integrations.autonomous_agents.turns import build_full_reasoning_prompt, build_watch_summary_message
from trade_integrations.autonomous_agents.mandate import mandate_config_from_agent
from trade_integrations.execution.enforce import bridge_watch_required, is_bridge_autonomous_agent
from trade_integrations.execution.profile import resolve_profile

logger = logging.getLogger(__name__)


def _ensure_trade_integrations_on_path() -> None:
    from pathlib import Path

    trade_root = Path(__file__).resolve().parents[3]
    integrations = trade_root / "integrations"
    if integrations.is_dir() and str(integrations) not in sys.path:
        sys.path.insert(0, str(integrations))


def _session_service():
    host = sys.modules.get("api_server") or sys.modules.get("agent.api_server")
    if host is None:
        return None
    return host._get_session_service()


async def _append_watch_system_message(session_id: str, summary: str) -> None:
    """Post a watch summary to the agent session without triggering a user turn."""
    if not summary.strip():
        return
    svc = _session_service()
    if not svc or not session_id:
        return
    try:
        await svc.send_message(session_id, summary, role="system")
    except Exception as exc:
        logger.warning("failed to append watch summary to session %s: %s", session_id, exc)


def _detached_nautilus_watching(agent_id: str) -> bool:
    """True when the detached Nautilus watch process owns polling for this agent."""
    try:
        from trade_integrations.autonomous_agents.nautilus_watch import (
            get_watch_process_status,
            is_agent_in_registry,
        )

        status = get_watch_process_status()
        if not status.get("alive"):
            return False
        if is_agent_in_registry(agent_id):
            return True
        bound = str(status.get("bound_agent_id") or "").strip()
        return bool(bound) and bound == agent_id
    except Exception:
        return False


def should_post_watch_to_chat(*, agent: dict[str, Any], feedback: dict[str, Any], market_closed: bool) -> bool:
    """Whether a watch tick should append a system line to the agent session chat."""
    if market_closed:
        return False
    if _detached_nautilus_watching(str(agent.get("id") or "")):
        return bool(feedback.get("requires_action")) or bool(feedback.get("alerts"))
    return True


def _persist_watch_state(
    agent: dict[str, Any],
    *,
    summary: str,
    feedback: dict[str, Any] | None = None,
    status: str = "watch",
) -> None:
    now = datetime.now(timezone.utc).isoformat()
    agent["last_watch_at"] = now
    payload = {
        "at": now,
        "status": status,
        "summary": summary,
        "requires_action": bool((feedback or {}).get("requires_action")),
        "alerts": list((feedback or {}).get("alerts") or [])[:3],
        "focus_ticker": (feedback or {}).get("focus_ticker"),
    }
    agent["last_watch_summary"] = payload
    save_agent(agent)


def _nautilus_watch_enabled() -> bool:
    try:
        from nautilus_openalgo_bridge.config import is_watch_enabled

        return is_watch_enabled()
    except ImportError:
        import os

        raw = os.getenv("NAUTILUS_WATCH_ENABLE", "true").strip().lower()
        return raw not in {"0", "false", "no", "off"}


def _watch_path_label(profile, *, detached: bool, scheduler_poll: bool) -> str:
    if detached:
        return "nautilus_detached"
    if scheduler_poll:
        return "nautilus_scheduler_poll"
    return "nautilus_bridge"


async def _run_nautilus_poll_tick(
    *,
    agent_id: str,
    agent: dict[str, Any],
    profile,
    focus: str,
    session_id: str,
    market: str = "IN",
) -> dict[str, Any]:
    """Scheduler inline poll for Nautilus watch rules."""
    from nautilus_openalgo_bridge.runtime.poll_loop import run_once

    bridge = run_once(
        agent_id=agent_id,
        trigger_vibe=True,
        process_intents=bool(profile.uses_nautilus_handoff),
    )

    alerts = list(bridge.get("alerts") or [])
    feedback = {
        "alerts": [str(a.get("message") or a) for a in alerts[:3]],
        "requires_action": bool(alerts),
        "focus_ticker": focus,
    }
    summary = build_watch_summary_message(agent=agent, feedback=feedback)
    _persist_watch_state(agent, summary=summary, feedback=feedback, status="watch")
    if should_post_watch_to_chat(agent=agent, feedback=feedback, market_closed=False):
        await _append_watch_system_message(session_id, summary)
    return {
        "status": "watch",
        "summary": summary,
        "watch_path": _watch_path_label(profile, detached=False, scheduler_poll=True),
        "nautilus_primary": False,
        "bridge": bridge,
    }


async def run_watch_tick(agent_id: str) -> dict[str, Any]:
    """Run a lightweight watch tick; dispatch full reasoning if alerts fire."""
    result = await _run_watch_tick_impl(agent_id)
    try:
        from trade_integrations.stock_simulator.integration import maybe_advance_sim_after_watch

        step_info = maybe_advance_sim_after_watch()
        if step_info and isinstance(result, dict):
            result["sim_step"] = step_info
    except Exception:
        pass
    return result


async def _run_watch_tick_impl(agent_id: str) -> dict[str, Any]:
    agent = get_agent(agent_id)
    if not agent or str(agent.get("status")) != "running":
        return {"skipped": True, "reason": "agent_not_running"}

    _ensure_trade_integrations_on_path()
    mc = mandate_config_from_agent(agent)
    symbols = list(agent.get("symbols") or ["NIFTY"])
    focus = symbols[0]

    session_id = str(agent.get("vibe_session_id") or "")

    profile = resolve_profile(agent=agent)
    if mc.market_hours_only:
        from nautilus_openalgo_bridge.market_hours import is_market_open_for_market

        market = "US" if profile.is_us else "IN"
        sim_open = False
        if market == "IN":
            try:
                from trade_integrations.stock_simulator.integration import sim_market_session_open

                sim_open = sim_market_session_open(market="IN")
            except Exception:
                sim_open = False
        if not sim_open and not is_market_open_for_market(market):
            summary = f"[autonomous_watch] {market} market closed — summary only"
            feedback = {"alerts": [], "requires_action": False, "focus_ticker": focus}
            _persist_watch_state(agent, summary=summary, feedback=feedback, status="closed")
            if should_post_watch_to_chat(agent=agent, feedback=feedback, market_closed=True):
                await _append_watch_system_message(session_id, summary)
            return {
                "status": "watch_only",
                "reason": "outside_market_hours",
                "market": market,
                "summary": summary,
            }

    nautilus_watch = profile.uses_nautilus_watch
    nautilus_primary = _detached_nautilus_watching(agent_id)

    if profile.uses_nautilus_handoff and not bridge_watch_required():
        summary = "[autonomous_watch] Nautilus bridge required for India agents — set NAUTILUS_WATCH_ENABLE=true"
        feedback = {"alerts": [summary], "requires_action": False, "focus_ticker": focus}
        _persist_watch_state(agent, summary=summary, feedback=feedback, status="degraded")
        if should_post_watch_to_chat(agent=agent, feedback=feedback, market_closed=False):
            await _append_watch_system_message(session_id, summary)
        return {"status": "degraded", "reason": "nautilus_watch_required", "summary": summary, "watch_path": "degraded"}

    if nautilus_primary and nautilus_watch:
        summary = build_watch_summary_message(
            agent=agent,
            feedback={"alerts": [], "requires_action": False, "focus_ticker": focus},
        )
        feedback = {"alerts": [], "requires_action": False, "focus_ticker": focus}
        _persist_watch_state(agent, summary=summary, feedback=feedback, status="watch")
        return {
            "status": "watch",
            "summary": summary,
            "watch_path": _watch_path_label(profile, detached=True, scheduler_poll=False),
            "nautilus_primary": True,
            "delegated_to_detached": True,
        }

    if nautilus_watch and profile.uses_nautilus_handoff and _nautilus_watch_enabled():
        try:
            return await _run_nautilus_poll_tick(
                agent_id=agent_id,
                agent=agent,
                profile=profile,
                focus=focus,
                session_id=session_id,
                market=profile.market,
            )
        except Exception as exc:
            logger.warning("nautilus bridge watch tick failed for %s: %s", agent_id, exc)
            summary = f"[autonomous_watch] Nautilus bridge error: {exc}"
            feedback = {"alerts": [summary], "requires_action": False, "focus_ticker": focus}
            _persist_watch_state(agent, summary=summary, feedback=feedback, status="error")
            if should_post_watch_to_chat(agent=agent, feedback=feedback, market_closed=False):
                await _append_watch_system_message(session_id, summary)
            return {
                "status": "error",
                "reason": "nautilus_bridge_failed",
                "summary": summary,
                "watch_path": "nautilus_scheduler_poll",
            }

    if is_bridge_autonomous_agent(agent_id):
        summary = "[autonomous_watch] India agent requires Nautilus bridge — autonomous_agents watch disabled"
        feedback = {"alerts": [summary], "requires_action": False, "focus_ticker": focus}
        _persist_watch_state(agent, summary=summary, feedback=feedback, status="degraded")
        if should_post_watch_to_chat(agent=agent, feedback=feedback, market_closed=False):
            await _append_watch_system_message(session_id, summary)
        return {
            "status": "degraded",
            "reason": "nautilus_watch_required",
            "summary": summary,
            "watch_path": "degraded",
        }

    feedback: dict[str, Any] = {"alerts": [], "requires_action": False, "focus_ticker": focus}
    summary = "[autonomous_watch] Nautilus bridge required — legacy autonomous_agents watch removed"
    _persist_watch_state(agent, summary=summary, feedback=feedback, status="degraded")
    if should_post_watch_to_chat(agent=agent, feedback=feedback, market_closed=False):
        await _append_watch_system_message(session_id, summary)
    return {
        "status": "degraded",
        "reason": "nautilus_watch_required",
        "summary": summary,
        "watch_path": "degraded",
    }


def _research_turn_recently_ran(agent: dict[str, Any], *, cooldown_min: float = 15.0) -> bool:
    last_at = str(agent.get("last_full_reasoning_at") or "")
    if not last_at:
        return False
    try:
        dt = datetime.fromisoformat(last_at.replace("Z", "+00:00"))
        age_min = (datetime.now(timezone.utc) - dt).total_seconds() / 60.0
    except ValueError:
        return False
    if age_min > cooldown_min:
        return False
    last_revision = str(agent.get("last_revision_at") or "")
    if last_revision and last_revision > last_at:
        return False
    return True


async def dispatch_full_reasoning(agent_id: str, *, turn_kind: str = "research") -> bool:
    """Enqueue a full reasoning turn on the agent's bound session. Returns True if dispatched."""
    agent = get_agent(agent_id)
    if not agent or str(agent.get("status")) != "running":
        return False

    if turn_kind in {"strategy_revision", "research", "post_execution"}:
        try:
            from trade_integrations.autonomous_agents.plan_approval import is_plan_approved

            if not is_plan_approved(agent):
                logger.info(
                    "skip %s turn for %s: plan not yet approved",
                    turn_kind,
                    agent_id,
                )
                return False
        except ImportError:
            pass

    if turn_kind == "research" and _research_turn_recently_ran(agent):
        logger.info("skip research turn for %s: recent full reasoning within cooldown", agent_id)
        return False

    if str(agent.get("bootstrap_status") or "") in {"pending", "running"} and turn_kind == "research":
        logger.info("skip research turn for %s: bootstrap still in flight", agent_id)
        return False

    if agent.get("streaming"):
        logger.info("skip full reasoning for %s: turn already in flight", agent_id)
        return False

    prefetch_note = ""
    if turn_kind in {"strategy_revision", "research", "post_execution"}:
        try:
            from trade_integrations.autonomous_agents.research_prefetch import prefetch_turn_research

            await prefetch_turn_research(agent_id, turn_kind=turn_kind)
        except Exception as exc:
            logger.warning("prefetch_turn_research failed for %s: %s", agent_id, exc)
            prefetch_note = (
                f"\n## Research prefetch warning\n"
                f"Hub/debate prefetch failed: {exc}. Call research tools with refresh=true before deciding.\n"
            )

    svc = _session_service()
    session_id = str(agent.get("vibe_session_id") or "")
    if not svc or not session_id:
        logger.warning("no session service for autonomous agent %s", agent_id)
        return False

    from trade_integrations.autonomous_agents.turns import build_autonomous_turn_prompt

    prompt = build_autonomous_turn_prompt(agent=agent, turn_kind=turn_kind) + prefetch_note
    agent["streaming"] = True
    agent["active_turn_kind"] = turn_kind
    agent["last_full_reasoning_at"] = datetime.now(timezone.utc).isoformat()
    if turn_kind == "strategy_revision":
        agent["last_revision_at"] = agent["last_full_reasoning_at"]
    elif turn_kind == "post_execution":
        agent["last_post_execution_at"] = agent["last_full_reasoning_at"]
    save_agent(agent)

    try:
        await svc.send_message(session_id, prompt)
        return True
    except Exception:
        latest = get_agent(agent_id) or agent
        latest["streaming"] = False
        save_agent(latest)
        raise
