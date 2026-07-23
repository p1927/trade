"""Post-commit bootstrap — immediate watch tick + first reasoning turn."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from trade_integrations.autonomous_agents.store import get_agent, save_agent
from trade_integrations.autonomous_agents.watch import dispatch_full_reasoning, run_watch_tick

logger = logging.getLogger(__name__)


def _bootstrap_structured_plan_ready(agent: dict) -> bool:
    """Options agents need structured legs in thesis/recommended before plan approval."""
    from trade_integrations.execution.profile import resolve_profile

    profile = resolve_profile(agent=agent)
    if "options" not in profile.allowed_instruments:
        return True
    thesis = dict(agent.get("thesis") or {})
    recommended = dict(thesis.get("recommended") or thesis.get("strategy") or {})
    legs = recommended.get("legs") or recommended.get("implementation_legs") or []
    if isinstance(legs, list) and len(legs) >= 1:
        return True
    last = dict(agent.get("last_decision") or {})
    widget_id = str(last.get("widget_id") or "")
    if widget_id:
        try:
            from trade_integrations.trade_widgets.store import load_trade_widget

            widget = load_trade_widget(widget_id)
            if widget:
                rec = dict(widget.get("recommended") or {})
                wlegs = rec.get("legs") or []
                if isinstance(wlegs, list) and len(wlegs) >= 1:
                    return True
        except Exception:
            logger.debug("bootstrap widget leg check skipped", exc_info=True)
    return False


def finalize_bootstrap_if_ready(agent_id: str) -> bool:
    """Move to plan approval once bootstrap decision + watch_spec are recorded."""
    agent = get_agent(agent_id)
    if not agent or str(agent.get("bootstrap_status")) != "running":
        return False
    if not agent.get("last_decision"):
        return False
    if not _bootstrap_structured_plan_ready(agent):
        return False

    now = datetime.now(timezone.utc).isoformat()
    agent["bootstrap_status"] = "done"
    agent["plan_approved_at"] = now
    agent["bootstrap_completed_at"] = now
    agent.pop("plan_approval_required", None)
    agent.pop("bootstrap_error", None)
    save_agent(agent)
    logger.info("agent %s bootstrap complete — autonomous watch active", agent_id)

    from trade_integrations.autonomous_agents.plan_approval import activate_agent_watch_after_approval

    activate_agent_watch_after_approval(agent_id, agent)

    try:
        import sys

        host = sys.modules.get("api_server") or sys.modules.get("agent.api_server")
        svc = host._get_session_service() if host else None
        session_id = str(agent.get("vibe_session_id") or "")
        if svc and session_id:
            svc.event_bus.emit(
                session_id,
                "autonomous_agent.plan_ready",
                {
                    "agent_id": agent_id,
                    "bootstrap_status": "done",
                    "plan_approved_at": now,
                    "strategy": (agent.get("thesis") or {}).get("strategy"),
                },
            )
    except Exception as exc:
        logger.debug("plan_ready emit failed for %s: %s", agent_id, exc)
    return True


async def _prefetch_bootstrap_research(agent_id: str) -> None:
    from trade_integrations.autonomous_agents.research_prefetch import prefetch_bootstrap_research

    await prefetch_bootstrap_research(agent_id)


async def bootstrap_agent(agent_id: str) -> None:
    """Run first watch tick and bootstrap research turn for a newly committed agent."""
    agent = get_agent(agent_id)
    if not agent or str(agent.get("status")) != "running":
        return

    bootstrap = str(agent.get("bootstrap_status") or "")
    if bootstrap == "done":
        return
    if agent.get("streaming"):
        logger.info("skip bootstrap for %s: turn already in flight", agent_id)
        return
    if bootstrap == "running":
        logger.info("skip bootstrap for %s: bootstrap already running", agent_id)
        return
    if bootstrap not in {"pending", "failed", ""}:
        return

    agent["bootstrap_status"] = "running"
    agent.pop("bootstrap_error", None)
    save_agent(agent)

    session_id = str(agent.get("vibe_session_id") or "")
    if session_id:
        from trade_integrations.autonomous_agents.watch import _append_watch_system_message

        await _append_watch_system_message(
            session_id,
            "Bootstrap starting — running first watch tick and research turn. Activity will appear here shortly.",
        )

    try:
        from trade_integrations.stock_simulator.integration import is_simulator_active
        from trade_integrations.stock_simulator.sim_runs import start_run

        if is_simulator_active():
            constraints = dict(agent.get("constraints") or {})
            budget = constraints.get("budget_inr")
            start_run(
                agent_id=agent_id,
                starting_capital=float(budget) if budget is not None else None,
            )
    except Exception:
        logger.debug("sim eval run start skipped", exc_info=True)

    try:
        # Warm hub/debate in background — do not block the first Vibe turn on TradingAgents.
        prefetch_task = asyncio.create_task(
            _prefetch_bootstrap_research(agent_id),
            name=f"bootstrap-prefetch-{agent_id[:12]}",
        )

        def _log_prefetch_result(task: asyncio.Task) -> None:
            try:
                task.result()
            except asyncio.CancelledError:
                pass
            except Exception as exc:
                logger.warning("bootstrap prefetch failed for %s: %s", agent_id, exc)

        prefetch_task.add_done_callback(_log_prefetch_result)

        await run_watch_tick(agent_id)
        dispatched = await dispatch_full_reasoning(agent_id, turn_kind="bootstrap")
        if not dispatched:
            latest = get_agent(agent_id) or agent
            if latest.get("streaming"):
                logger.info("bootstrap dispatch skipped for %s: turn already in flight", agent_id)
                return
            raise RuntimeError("bootstrap research turn was not dispatched (session unavailable or turn in flight)")
    except Exception as exc:
        logger.warning("bootstrap failed for %s: %s", agent_id, exc, exc_info=True)
        latest = get_agent(agent_id) or agent
        latest["bootstrap_status"] = "failed"
        latest["bootstrap_error"] = str(exc)[:500]
        latest["bootstrap_completed_at"] = datetime.now(timezone.utc).isoformat()
        save_agent(latest)
        return

    # bootstrap_status stays "running" until record_autonomous_decision + finalize
