"""Lightweight watch ticks and alert detection."""

from __future__ import annotations

import logging
import sys
from datetime import datetime, timezone
from typing import Any

from trade_integrations.autonomous_agents.store import get_agent, save_agent
from trade_integrations.autonomous_agents.turns import build_full_reasoning_prompt, build_watch_summary_message
from trade_integrations.auto_paper.config import get_auto_paper_config
from trade_integrations.auto_paper.engine import is_market_session_open
from trade_integrations.auto_paper.mandate_config import mandate_config_from_agent
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


def _nautilus_watch_enabled() -> bool:
    try:
        from nautilus_openalgo_bridge.config import is_watch_enabled

        return is_watch_enabled()
    except ImportError:
        import os

        raw = os.getenv("NAUTILUS_WATCH_ENABLE", "true").strip().lower()
        return raw not in {"0", "false", "no", "off"}


async def run_watch_tick(agent_id: str) -> dict[str, Any]:
    """Run a lightweight watch tick; dispatch full reasoning if alerts fire."""
    agent = get_agent(agent_id)
    if not agent or str(agent.get("status")) != "running":
        return {"skipped": True, "reason": "agent_not_running"}

    _ensure_trade_integrations_on_path()
    mc = mandate_config_from_agent(agent)
    cfg = get_auto_paper_config()
    symbols = list(agent.get("symbols") or ["NIFTY"])
    focus = symbols[0]

    session_id = str(agent.get("vibe_session_id") or "")

    if mc.market_hours_only and not is_market_session_open(cfg):
        now = datetime.now(timezone.utc).isoformat()
        agent["last_watch_at"] = now
        save_agent(agent)
        summary = "[autonomous_watch] market closed — summary only"
        await _append_watch_system_message(session_id, summary)
        return {"status": "watch_only", "reason": "outside_market_hours", "summary": summary}

    profile = resolve_profile(agent=agent)
    bridge_agent = profile.uses_nautilus_handoff

    if bridge_agent and not bridge_watch_required():
        now = datetime.now(timezone.utc).isoformat()
        agent["last_watch_at"] = now
        save_agent(agent)
        summary = "[autonomous_watch] Nautilus bridge required for India agents — set NAUTILUS_WATCH_ENABLE=true"
        await _append_watch_system_message(session_id, summary)
        return {"status": "degraded", "reason": "nautilus_watch_required", "summary": summary}

    if bridge_agent or _nautilus_watch_enabled():
        now = datetime.now(timezone.utc).isoformat()
        agent["last_watch_at"] = now
        save_agent(agent)
        try:
            from nautilus_openalgo_bridge.runtime.poll_loop import run_once

            bridge = run_once(agent_id=agent_id, trigger_vibe=True, process_intents=True)
            alerts = list(bridge.get("alerts") or [])
            summary = build_watch_summary_message(
                agent=agent,
                feedback={
                    "alerts": [str(a.get("message") or a) for a in alerts[:3]],
                    "requires_action": bool(alerts),
                    "focus_ticker": focus,
                },
            )
            await _append_watch_system_message(session_id, summary)
            return {
                "status": "watch",
                "summary": summary,
                "watch_path": "nautilus_bridge",
                "nautilus_primary": True,
                "bridge": bridge,
            }
        except Exception as exc:
            logger.warning("nautilus bridge watch tick failed for %s: %s", agent_id, exc)
            summary = f"[autonomous_watch] Nautilus bridge error: {exc}"
            await _append_watch_system_message(session_id, summary)
            if bridge_agent:
                return {
                    "status": "error",
                    "reason": "nautilus_bridge_failed",
                    "summary": summary,
                    "watch_path": "nautilus_bridge",
                }
            return {
                "status": "watch",
                "summary": summary,
                "nautilus_primary": True,
            }

    if is_bridge_autonomous_agent(agent_id):
        now = datetime.now(timezone.utc).isoformat()
        agent["last_watch_at"] = now
        save_agent(agent)
        summary = "[autonomous_watch] India agent requires Nautilus bridge — auto_paper watch disabled"
        await _append_watch_system_message(session_id, summary)
        return {
            "status": "degraded",
            "reason": "nautilus_watch_required",
            "summary": summary,
        }

    feedback: dict[str, Any] = {}
    try:
        from trade_integrations.auto_paper.market_feedback import build_market_feedback

        feedback = build_market_feedback(ticker=focus)
    except Exception as exc:
        logger.warning("watch feedback failed for %s: %s", agent_id, exc)
        feedback = {"alerts": [f"feedback_error:{exc}"], "requires_action": False}

    now = datetime.now(timezone.utc).isoformat()
    agent["last_watch_at"] = now
    agent["last_market_feedback"] = feedback
    save_agent(agent)

    summary = build_watch_summary_message(agent=agent, feedback=feedback)
    await _append_watch_system_message(session_id, summary)

    requires_action = bool(feedback.get("requires_action")) or bool(feedback.get("alerts"))
    if requires_action and mc.revision_policy != "scheduled_only":
        await dispatch_full_reasoning(agent_id, turn_kind="strategy_revision")
        return {"status": "alert", "summary": summary, "feedback": feedback, "watch_path": "auto_paper_legacy"}

    return {"status": "watch", "summary": summary, "feedback": feedback, "watch_path": "auto_paper_legacy"}


async def dispatch_full_reasoning(agent_id: str, *, turn_kind: str = "research") -> bool:
    """Enqueue a full reasoning turn on the agent's bound session. Returns True if dispatched."""
    agent = get_agent(agent_id)
    if not agent or str(agent.get("status")) != "running":
        return False

    if agent.get("streaming"):
        logger.info("skip full reasoning for %s: turn already in flight", agent_id)
        return False

    svc = _session_service()
    session_id = str(agent.get("vibe_session_id") or "")
    if not svc or not session_id:
        logger.warning("no session service for autonomous agent %s", agent_id)
        return False

    prompt = build_full_reasoning_prompt(agent=agent, turn_kind=turn_kind)
    agent["streaming"] = True
    agent["last_full_reasoning_at"] = datetime.now(timezone.utc).isoformat()
    if turn_kind == "strategy_revision":
        agent["last_revision_at"] = agent["last_full_reasoning_at"]
    save_agent(agent)

    try:
        await svc.send_message(session_id, prompt)
        return True
    except Exception:
        latest = get_agent(agent_id) or agent
        latest["streaming"] = False
        save_agent(latest)
        raise
