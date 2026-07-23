"""Thesis-break helpers for options monitor → autonomous agent revision turns."""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


def build_thesis_break_prompt(
    *,
    ticker: str,
    widget_id: str,
    reasons: list[str],
    new_plan_widget_id: str | None = None,
) -> str:
    """Focused prompt when position monitor detects thesis break."""
    reason_text = "; ".join(reasons) or "thesis break"
    supersede_line = ""
    if new_plan_widget_id and new_plan_widget_id != widget_id:
        supersede_line = f"\n- Superseding refreshed plan widget: `{new_plan_widget_id}`"
    return f"""# URGENT: Thesis break on open position

Autonomous agent session — **no user confirmation required** for paper/analyzer orders.

- Underlying: **{ticker}**
- Open position widget: `{widget_id}`{supersede_line}
- Break reasons: {reason_text}

1. Load live context (`get_market_feedback` / hub research for {ticker})
2. `get_plan_position_status("{widget_id}")`
3. Decide: **EXIT** (close positions) or **HOLD** / **REVISE** with strong rationale
4. `record_autonomous_decision` with EXIT, HOLD, or REVISE plus confidence and strategy
5. If exited or revised, update `set_agent_watch_spec` when levels change
"""


def is_agent_session_active() -> bool:
    """True when at least one autonomous agent is running."""
    try:
        from trade_integrations.autonomous_agents.store import list_agents

        return any(str(a.get("status") or "") == "running" for a in list_agents())
    except Exception:
        return False


def _agent_id_from_ledger(widget_id: str | None) -> str | None:
    wid = str(widget_id or "").strip()
    if not wid:
        return None
    try:
        from trade_integrations.monitor.execution_ledger import get_ledger_entry

        entry = get_ledger_entry(wid)
        if not entry:
            return None
        agent_id = str(entry.get("agent_id") or "").strip()
        return agent_id or None
    except Exception:
        logger.debug("ledger agent lookup failed for widget %s", wid, exc_info=True)
        return None


def _agent_id_from_widget_file(widget_id: str | None) -> str | None:
    wid = str(widget_id or "").strip()
    if not wid:
        return None
    try:
        from trade_integrations.trade_widgets.store import load_trade_widget

        widget = load_trade_widget(wid)
        if not widget:
            return None
        agent_id = str(widget.get("autonomous_agent_id") or widget.get("agent_id") or "").strip()
        return agent_id or None
    except Exception:
        logger.debug("widget agent lookup failed for %s", wid, exc_info=True)
        return None


def _agent_matches_widget(agent: dict[str, Any], widget_id: str) -> bool:
    from trade_integrations.autonomous_agents.plan_approval import resolve_widget_id

    wid = str(widget_id).strip()
    active_widget = str(agent.get("active_trade_plan_widget_id") or resolve_widget_id(agent) or "").strip()
    if active_widget and active_widget == wid:
        return True
    watch = dict(agent.get("watch_spec") or {})
    return str(watch.get("widget_id") or "") == wid


def _activity_sort_key(agent: dict[str, Any]) -> str:
    for key in ("last_full_reasoning_at", "last_revision_at", "updated_at", "created_at"):
        value = str(agent.get(key) or "")
        if value:
            return value
    return ""


def _pick_agent_by_activity(agents: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not agents:
        return None
    if len(agents) == 1:
        return agents[0]
    ranked = sorted(agents, key=_activity_sort_key, reverse=True)
    return ranked[0]


def resolve_running_agent_for_symbol(
    underlying: str,
    *,
    widget_id: str | None = None,
) -> str | None:
    """Best-effort agent id for a thesis-break dispatch."""
    symbol = str(underlying or "").strip().upper()
    try:
        from trade_integrations.autonomous_agents.store import get_agent, list_agents

        running = [a for a in list_agents() if str(a.get("status") or "") == "running"]
        if not running:
            return None

        for lookup in (_agent_id_from_ledger, _agent_id_from_widget_file):
            agent_id = lookup(widget_id)
            if agent_id:
                agent = get_agent(agent_id)
                if agent and str(agent.get("status") or "") == "running":
                    return agent_id

        if widget_id:
            wid = str(widget_id).strip()
            widget_matches = [a for a in running if _agent_matches_widget(a, wid)]
            picked = _pick_agent_by_activity(widget_matches)
            if picked:
                return str(picked.get("id") or "") or None

        symbol_matches = [
            a
            for a in running
            if symbol in [str(s or "").strip().upper() for s in (a.get("symbols") or [])]
        ]
        picked = _pick_agent_by_activity(symbol_matches)
        if picked:
            return str(picked.get("id") or "") or None

        picked = _pick_agent_by_activity(running)
        if picked and len(running) > 1:
            logger.warning(
                "thesis break resolved via activity tie-break for %s widget=%s",
                symbol,
                widget_id,
            )
        return str(picked.get("id") or "") if picked else None
    except Exception:
        logger.debug("resolve_running_agent_for_symbol failed", exc_info=True)
    return None


async def dispatch_thesis_break_revision(
    *,
    underlying: str,
    widget_id: str,
    reasons: list[str],
    new_plan_widget_id: str | None = None,
) -> dict[str, Any]:
    """Enqueue a strategy-revision turn for the agent tied to this open position."""
    if not is_agent_session_active():
        return {"status": "skipped", "reason": "no_running_agent"}

    agent_id = resolve_running_agent_for_symbol(underlying, widget_id=widget_id)
    if not agent_id:
        logger.warning(
            "thesis break skipped: no agent resolved for %s widget=%s",
            underlying,
            widget_id,
        )
        return {"status": "skipped", "reason": "agent_not_resolved"}

    from trade_integrations.autonomous_agents.store import get_agent, save_agent
    from trade_integrations.autonomous_agents.watch import _session_service

    agent = get_agent(agent_id)
    if not agent or str(agent.get("status") or "") != "running":
        return {"status": "skipped", "reason": "agent_not_running"}
    if agent.get("streaming"):
        return {"status": "skipped", "reason": "turn_in_flight"}

    try:
        from trade_integrations.autonomous_agents.plan_approval import is_plan_approved

        if not is_plan_approved(agent):
            logger.info("thesis break skipped for %s: plan not approved", agent_id)
            return {"status": "skipped", "reason": "plan_not_approved"}
    except ImportError:
        pass

    pending = list(agent.get("pending_alerts") or [])
    prompt = build_thesis_break_prompt(
        ticker=underlying,
        widget_id=widget_id,
        reasons=reasons,
        new_plan_widget_id=new_plan_widget_id,
    )
    svc = _session_service()
    session_id = str(agent.get("vibe_session_id") or "").strip()
    if not svc or not session_id:
        return {
            "status": "error",
            "error": "session_runtime_unavailable",
            "agent_id": agent_id,
        }

    pending.append(
        {
            "type": "thesis_break",
            "widget_id": widget_id,
            "underlying": underlying,
            "reasons": list(reasons or []),
        }
    )
    agent["pending_alerts"] = pending[-20:]
    save_agent(agent)

    agent["streaming"] = True
    agent["last_revision_at"] = agent.get("last_full_reasoning_at")
    save_agent(agent)
    try:
        await svc.send_message(session_id, prompt)
        latest = get_agent(agent_id) or agent
        latest["pending_alerts"] = []
        save_agent(latest)
        return {"status": "dispatched", "agent_id": agent_id, "session_id": session_id}
    except Exception as exc:
        logger.warning("thesis break dispatch failed for %s: %s", agent_id, exc)
        latest = get_agent(agent_id) or agent
        latest["streaming"] = False
        save_agent(latest)
        return {"status": "error", "error": str(exc), "agent_id": agent_id}
