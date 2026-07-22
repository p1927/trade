"""MCP-facing actions for autonomous agents."""

from __future__ import annotations

from typing import Any

from trade_integrations.autonomous_agents.proposals import propose_autonomous_agent
from trade_integrations.autonomous_agents.store import get_agent, list_agents, load_proposal, save_agent
from trade_integrations.auto_paper.mcp_actions import get_status, record_decision
from trade_integrations.execution.profile import resolve_profile


def mcp_propose(**kwargs: Any) -> dict[str, Any]:
    return propose_autonomous_agent(**kwargs)


def mcp_get_status(agent_id: str | None = None) -> dict[str, Any]:
    if agent_id:
        agent = get_agent(agent_id)
        if not agent:
            return {"status": "error", "error": f"agent not found: {agent_id}"}
        profile = resolve_profile(agent=agent)
        bridge_status = None
        if profile.uses_nautilus_handoff:
            try:
                from trade_integrations.execution.bridge_intent import build_bridge_market_feedback

                bridge_status = build_bridge_market_feedback(
                    agent_id=agent_id,
                    ticker=(agent.get("symbols") or ["NIFTY"])[0],
                )
            except Exception:
                bridge_status = None
        paper_status = get_status(autonomous_agent_id=agent_id if profile.uses_openalgo_auto_paper else None)
        session = paper_status.get("session") or {}
        paper_active = bool(session.get("enabled"))
        session_agent = str(session.get("autonomous_agent_id") or "").strip()
        paper_matches = not session_agent or session_agent == agent_id
        return {
            "status": "ok",
            "agent": agent,
            "execution_profile": profile.prompt_fragment_id,
            "execution_market": profile.market,
            "execution_backend": profile.backend,
            "paper_session_active": paper_active if profile.uses_openalgo_auto_paper and paper_matches else None,
            "paper_session": session if profile.uses_openalgo_auto_paper and paper_matches else None,
            "paper_note": (
                "US agent — use Alpaca paper tools; OpenAlgo INR auto_paper session not used."
                if profile.is_us
                else (
                    "OpenAlgo paper session belongs to a different agent — ignore session P&L for this agent."
                    if paper_active and not paper_matches
                    else None
                )
            ),
            "mandate_config": agent.get("mandate_config") or paper_status.get("mandate_config"),
            "bridge_status": bridge_status,
            "watch_path": "nautilus_bridge" if profile.uses_nautilus_handoff else "legacy",
            "scheduler_health": paper_status.get("scheduler_health"),
            "market_open": paper_status.get("market_open"),
            "lifecycle": session.get("lifecycle"),
        }
    return {"status": "ok", "agents": list_agents(), "paper_status": get_status()}


def mcp_get_proposal(proposal_id: str) -> dict[str, Any]:
    proposal = load_proposal(proposal_id)
    if not proposal:
        return {"status": "error", "error": "proposal not found"}
    return {"status": "ok", "proposal": proposal}


def _normalize_confidence(value: int | float | str | None) -> int | None:
    if value is None:
        return None
    try:
        return max(0, min(100, int(value)))
    except (TypeError, ValueError):
        return None


def _merge_thesis_from_decision(
    agent: dict[str, Any],
    *,
    decision: str,
    rationale: str,
    confidence: int | None = None,
    direction: str | None = None,
    strategy: str | None = None,
) -> None:
    from datetime import datetime, timezone

    thesis = dict(agent.get("thesis") or {})
    thesis["decision"] = str(decision).strip().upper()
    thesis["rationale"] = rationale.strip()
    thesis["updated_at"] = datetime.now(timezone.utc).isoformat()
    if confidence is not None:
        thesis["confidence"] = confidence
    if direction:
        thesis["direction"] = direction.strip()
    if strategy:
        thesis["strategy"] = strategy.strip()
    agent["thesis"] = thesis


def _attach_decision_metadata(
    entry: dict[str, Any] | None,
    *,
    confidence: int | None,
    direction: str | None,
    strategy: str | None,
) -> dict[str, Any] | None:
    if not entry:
        return entry
    if confidence is not None:
        entry["confidence"] = confidence
    if direction:
        entry["direction"] = direction.strip()
    if strategy:
        entry["strategy"] = strategy.strip()
    return entry


def _record_sim_eval_decision(*, agent_id: str, decision: dict[str, Any]) -> None:
    try:
        from trade_integrations.stock_simulator.integration import is_simulator_active
        from trade_integrations.stock_simulator.sim_runs import record_decision

        if is_simulator_active():
            record_decision(agent_id=agent_id, decision=decision)
    except Exception:
        pass


def mcp_record_decision(
    *,
    agent_id: str,
    decision: str,
    rationale: str,
    ticker: str | None = None,
    actions_taken: list[str] | None = None,
    confidence: int | None = None,
    direction: str | None = None,
    strategy: str | None = None,
) -> dict[str, Any]:
    from datetime import datetime, timezone

    agent = get_agent(agent_id)
    if not agent:
        return {"status": "error", "error": f"agent not found: {agent_id}"}

    norm_confidence = _normalize_confidence(confidence)
    profile = resolve_profile(agent=agent)
    if not profile.uses_openalgo_auto_paper:
        entry = {
            "at": datetime.now(timezone.utc).isoformat(),
            "decision": str(decision).strip().upper(),
            "rationale": rationale.strip(),
            "ticker": (ticker or (agent.get("symbols") or ["SPY"])[0]).strip().upper(),
            "actions_taken": actions_taken or [],
            "execution_market": profile.market,
        }
        _attach_decision_metadata(
            entry,
            confidence=norm_confidence,
            direction=direction,
            strategy=strategy,
        )
        agent["last_decision"] = entry
        _merge_thesis_from_decision(
            agent,
            decision=decision,
            rationale=rationale,
            confidence=norm_confidence,
            direction=direction,
            strategy=strategy,
        )
        if entry["decision"] in {"REVISE", "ADJUST"}:
            agent["last_revision_at"] = entry["at"]
        save_agent(agent)
        try:
            from trade_integrations.autonomous_agents.bootstrap import finalize_bootstrap_if_ready

            finalize_bootstrap_if_ready(agent_id)
        except Exception:
            pass
        _record_sim_eval_decision(agent_id=agent_id, decision=entry)
        return {
            "status": "ok",
            "agent_id": agent_id,
            "decision": entry,
            "thesis": agent.get("thesis"),
            "paper_note": f"{profile.market} agent — decision stored on agent record (not OpenAlgo session).",
        }

    result = record_decision(
        decision=decision,
        rationale=rationale,
        ticker=ticker,
        actions_taken=actions_taken,
        confidence=norm_confidence,
        direction=direction,
        strategy=strategy,
    )
    agent = get_agent(agent_id) or agent
    last = dict(result.get("decision") or {})
    _attach_decision_metadata(
        last,
        confidence=norm_confidence,
        direction=direction,
        strategy=strategy,
    )
    agent["last_decision"] = last
    _merge_thesis_from_decision(
        agent,
        decision=decision,
        rationale=rationale,
        confidence=norm_confidence,
        direction=direction,
        strategy=strategy,
    )
    decision_upper = str(decision).upper()
    if decision_upper in {"REVISE", "ADJUST"}:
        agent["last_revision_at"] = last.get("at")
    if decision_upper == "EXIT":
        from nautilus_openalgo_bridge.handoff import clear_agent_position_state

        clear_agent_position_state(agent_id)
    elif decision_upper in {"ENTER", "REVISE", "ADJUST"}:
        try:
            from nautilus_openalgo_bridge.reconcile import sync_handoff_from_position_book

            sync_handoff_from_position_book(agent_id, underlying=ticker)
        except Exception:
            pass
    save_agent(agent)
    try:
        from trade_integrations.autonomous_agents.bootstrap import finalize_bootstrap_if_ready

        finalize_bootstrap_if_ready(agent_id)
    except Exception:
        pass
    _record_sim_eval_decision(agent_id=agent_id, decision=last)
    return {
        "status": "ok",
        "agent_id": agent_id,
        **{k: v for k, v in result.items() if k != "status"},
        "thesis": agent.get("thesis"),
    }


def mcp_set_watch_spec(
    agent_id: str,
    watch_spec: dict[str, Any] | None = None,
    *,
    strategy: str | None = None,
    spot: float | None = None,
    target: float | None = None,
    stop: float | None = None,
) -> dict[str, Any]:
    agent = get_agent(agent_id)
    if not agent:
        return {"status": "error", "error": f"agent not found: {agent_id}"}
    profile = resolve_profile(agent=agent)

    strategy_name = strategy or (watch_spec or {}).get("strategy") or (agent.get("thesis") or {}).get("strategy")
    if strategy_name:
        from trade_integrations.auto_paper.mandate_config import mandate_config_from_agent
        from trade_integrations.autonomous_agents.strategy_watch_spec import (
            build_watch_spec_for_strategy,
            format_watch_spec_summary,
        )

        mc = mandate_config_from_agent(agent)
        symbols = list(agent.get("symbols") or ["NIFTY"])
        watch_spec = build_watch_spec_for_strategy(
            strategy=str(strategy_name),
            mandate=mc,
            symbols=symbols,
            spot=spot,
            target=target,
            stop=stop,
        )
    elif not watch_spec:
        return {"status": "error", "error": "provide strategy name or explicit watch_spec"}

    agent["watch_spec"] = watch_spec
    mc_dict = dict(agent.get("mandate_config") or {})
    mc_dict["watch_spec"] = watch_spec
    agent["mandate_config"] = mc_dict
    save_agent(agent)

    summary = ""
    try:
        from trade_integrations.autonomous_agents.strategy_watch_spec import format_watch_spec_summary

        summary = format_watch_spec_summary(watch_spec)
    except Exception:
        pass

    handoff = None
    if profile.uses_nautilus_watch:
        from nautilus_openalgo_bridge.handoff import sync_watch_spec_to_handoff

        handoff = sync_watch_spec_to_handoff(agent_id, watch_spec)
        if profile.uses_nautilus_handoff:
            try:
                from nautilus_openalgo_bridge.reconcile import sync_handoff_from_position_book

                sync_handoff_from_position_book(agent_id, underlying=str(agent.get("symbols", ["NIFTY"])[0]))
            except Exception:
                pass

    _maybe_post_watchers_system_message(agent, summary)

    return {
        "status": "ok",
        "agent_id": agent_id,
        "watch_spec": watch_spec,
        "watch_summary": summary,
        "handoff_synced": handoff is not None,
    }


def _maybe_post_watchers_system_message(agent: dict[str, Any], summary: str) -> None:
    if not summary:
        return
    session_id = str(agent.get("vibe_session_id") or "").strip()
    if not session_id:
        return
    try:
        import sys

        host = sys.modules.get("api_server") or sys.modules.get("agent.api_server")
        svc = host._get_session_service() if host else None
        if not svc:
            return
        symbols = list(agent.get("symbols") or [])
        focus = symbols[0] if symbols else "?"
        msg = f"[autonomous_watchers] {focus} — {summary}"
        import asyncio

        loop = asyncio.get_event_loop()
        if loop.is_running():
            asyncio.create_task(svc.send_message(session_id, msg, role="system"))
        else:
            loop.run_until_complete(svc.send_message(session_id, msg, role="system"))
    except Exception:
        pass


def mcp_get_quant_monitor_status(agent_id: str) -> dict[str, Any]:
    from trade_integrations.monitor.quant_monitor import get_quant_monitor_status

    agent = get_agent(agent_id)
    if not agent:
        return {"status": "error", "error": f"agent not found: {agent_id}"}
    return {"status": "ok", **get_quant_monitor_status(agent_id)}


def mcp_submit_bridge_execution_intent(
    *,
    agent_id: str,
    action: str,
    rationale: str,
    widget_id: str | None = None,
    underlying: str | None = None,
) -> dict[str, Any]:
    """Submit ENTER/ADJUST/EXIT intent through nautilus_openalgo_bridge → OpenAlgo."""
    from nautilus_openalgo_bridge.intent_queue import process_pending_intents, submit_intent
    from nautilus_openalgo_bridge.models import ExecutionIntent, IntentAction

    agent = get_agent(agent_id)
    if not agent:
        return {"status": "error", "error": f"agent not found: {agent_id}"}

    profile = resolve_profile(agent=agent)
    if not profile.uses_nautilus_handoff:
        return {"status": "error", "error": "bridge intents are for India autonomous agents only"}

    action_upper = action.strip().upper()
    if action_upper not in IntentAction.__members__:
        return {"status": "error", "error": f"invalid action: {action}"}

    intent_action = IntentAction(action_upper)
    legs: list = []
    if intent_action == IntentAction.EXIT:
        from trade_integrations.execution.bridge_intent import submit_exit_intent

        return submit_exit_intent(agent_id=agent_id, rationale=rationale, underlying=underlying)

    if widget_id:
        from trade_integrations.trade_widgets.store import load_trade_widget
        from trade_integrations.execution.bridge_intent import legs_from_widget
        from nautilus_openalgo_bridge.models import ExecutionLeg
        from trade_integrations.auto_paper.mandate_config import mandate_config_from_agent

        widget = load_trade_widget(widget_id)
        if not widget:
            return {"status": "error", "error": f"widget not found: {widget_id}"}
        mc = mandate_config_from_agent(agent)
        legs = [ExecutionLeg.from_dict(row) for row in legs_from_widget(widget, product=mc.resolve_product())]

    intent = ExecutionIntent(
        action=intent_action,
        agent_id=agent_id,
        rationale=rationale,
        legs=legs,
        widget_id=widget_id,
        underlying=(underlying or (agent.get("symbols") or ["NIFTY"])[0]).upper(),
        strategy="vibe_bridge_intent",
    )
    path = submit_intent(intent)
    results = process_pending_intents(max_count=1)
    return {"status": "submitted", "path": str(path), "results": results}
