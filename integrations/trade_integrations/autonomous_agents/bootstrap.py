"""Post-commit bootstrap — immediate watch tick + first reasoning turn."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from trade_integrations.autonomous_agents.store import get_agent, save_agent
from trade_integrations.autonomous_agents.watch import dispatch_full_reasoning, run_watch_tick

logger = logging.getLogger(__name__)

_bootstrap_locks: dict[str, asyncio.Lock] = {}

_DEFAULT_BOOTSTRAP_TIMEOUT_S = 540.0


def _bootstrap_timeout_seconds() -> float:
    import os

    raw = os.getenv("AUTONOMOUS_BOOTSTRAP_TIMEOUT_S", str(_DEFAULT_BOOTSTRAP_TIMEOUT_S)).strip()
    try:
        return max(60.0, float(raw))
    except ValueError:
        return _DEFAULT_BOOTSTRAP_TIMEOUT_S


def is_bootstrap_coroutine_active(agent_id: str) -> bool:
    """True when a bootstrap_agent coroutine currently holds the per-agent lock."""
    lock = _bootstrap_locks.get(str(agent_id or "").strip())
    return lock is not None and lock.locked()


def _coerce_watch_rules(rules: object) -> list[dict]:
    """Normalize watch_spec.rules whether stored as list or MCP/XML-style dict."""
    if isinstance(rules, list):
        return [r for r in rules if isinstance(r, dict)]
    if isinstance(rules, dict):
        item = rules.get("item")
        if isinstance(item, list):
            return [r for r in item if isinstance(r, dict)]
        if isinstance(item, dict):
            return [item]
        if rules.get("symbol") or rules.get("metric"):
            return [rules]
    return []


def _thesis_recommended_dict(thesis: dict) -> dict:
    recommended = thesis.get("recommended")
    if isinstance(recommended, dict):
        return recommended
    strategy = thesis.get("strategy")
    if isinstance(strategy, dict):
        return strategy
    return {}


def _bootstrap_structured_plan_ready(agent: dict) -> bool:
    """Options agents need structured legs in thesis/recommended before plan approval."""
    from trade_integrations.execution.profile import resolve_profile

    profile = resolve_profile(agent=agent)
    if "options" not in profile.allowed_instruments:
        return True
    thesis = dict(agent.get("thesis") or {})
    strategy_name = thesis.get("strategy")
    if isinstance(strategy_name, str):
        key = strategy_name.strip().lower().replace(" ", "_").replace("-", "_")
        if key in {"hold_cash", "hold", "skip", "wait"}:
            return True
    recommended = _thesis_recommended_dict(thesis)
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


def _bootstrap_watch_spec_ready(agent: dict) -> bool:
    """Bootstrap must persist strategy-encoded watchers before finalize."""
    for spec_source in (
        dict(agent.get("watch_spec") or {}),
        dict((agent.get("mandate_config") or {}).get("watch_spec") or {}),
    ):
        if _coerce_watch_rules(spec_source.get("rules")):
            return True
    return False


def bootstrap_finalize_prerequisites_met(agent: dict) -> bool:
    """True when bootstrap can safely auto-finalize (structured plan + watch_spec)."""
    return _bootstrap_structured_plan_ready(agent) and _bootstrap_watch_spec_ready(agent)


def safe_finalize_bootstrap_if_ready(agent_id: str) -> bool:
    """Finalize bootstrap; log failures instead of swallowing them silently."""
    try:
        return finalize_bootstrap_if_ready(agent_id)
    except Exception:
        logger.warning("finalize_bootstrap_if_ready failed for %s", agent_id, exc_info=True)
        return False


def finalize_bootstrap_if_ready(agent_id: str) -> bool:
    """Enter plan-approval gate once decision, structured plan, and watch_spec are ready."""
    agent = get_agent(agent_id)
    if not agent or str(agent.get("bootstrap_status")) != "running":
        return False
    if not agent.get("last_decision"):
        return False
    if not bootstrap_finalize_prerequisites_met(agent):
        return False

    from trade_integrations.autonomous_agents.plan_approval import resolve_widget_id

    now = datetime.now(timezone.utc).isoformat()
    agent["bootstrap_status"] = "awaiting_plan_approval"
    agent["plan_approval_required"] = True
    agent["plan_revision_source"] = "bootstrap"
    wid = resolve_widget_id(agent)
    if wid:
        agent["active_trade_plan_widget_id"] = wid
    agent.pop("bootstrap_error", None)
    save_agent(agent)
    logger.info("agent %s bootstrap ready — awaiting user plan approval", agent_id)

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
                    "bootstrap_status": "awaiting_plan_approval",
                    "active_trade_plan_widget_id": wid or None,
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
    aid = str(agent_id or "").strip()
    if not aid:
        return
    lock = _bootstrap_locks.setdefault(aid, asyncio.Lock())
    if lock.locked():
        logger.info("skip bootstrap for %s: coroutine already active", aid)
        return
    async with lock:
        try:
            await asyncio.wait_for(
                _bootstrap_agent_locked(aid),
                timeout=_bootstrap_timeout_seconds(),
            )
        except asyncio.TimeoutError:
            logger.warning(
                "bootstrap timed out for %s after %.0fs",
                aid,
                _bootstrap_timeout_seconds(),
            )
            latest = get_agent(aid) or {}
            if str(latest.get("bootstrap_status") or "") == "running":
                latest["bootstrap_status"] = "failed"
                latest["bootstrap_error"] = (
                    f"bootstrap timed out after {int(_bootstrap_timeout_seconds())}s"
                )
                latest["bootstrap_completed_at"] = datetime.now(timezone.utc).isoformat()
                save_agent(latest)


async def _bootstrap_agent_locked(agent_id: str) -> None:
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
