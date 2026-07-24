"""MCP-facing actions for autonomous agents."""

from __future__ import annotations

import json
import logging
from typing import Any

from trade_integrations.autonomous_agents.proposals import propose_autonomous_agent
from trade_integrations.autonomous_agents.store import get_agent, list_agents, load_agent, load_proposal, save_agent
from trade_integrations.autonomous_agents.agent_status import get_agent_execution_status, load_openalgo_authority
from trade_integrations.autonomous_agents.decisions import record_agent_decision
from trade_integrations.execution.profile import resolve_profile, resolve_profile_from_context

logger = logging.getLogger(__name__)


def activate_watch_spec_for_agent(
    agent_id: str,
    agent: dict[str, Any],
    watch_spec: dict[str, Any],
    *,
    profile: Any | None = None,
) -> Any:
    """Sync watch registry + Nautilus handoff (deferred until plan approval during bootstrap)."""
    profile = profile or resolve_profile(agent=agent)
    handoff = None
    if not profile.uses_nautilus_watch:
        return handoff

    vibe_sid = str(agent.get("vibe_session_id") or "").strip()
    try:
        from trade_integrations.watch_registry.store import create_watch, list_watches, update_watch

        existing = list_watches(owner_kind="autonomous_agent", owner_id=agent_id, active_only=True)
        if existing:
            update_watch(str(existing[0].get("watch_id")), watch_spec=watch_spec)
        elif vibe_sid:
            create_watch(
                owner_kind="autonomous_agent",
                owner_id=agent_id,
                vibe_session_id=vibe_sid,
                watch_spec=watch_spec,
                symbols=list(agent.get("symbols") or []),
                label="strategy watch",
            )
    except Exception:
        logger.warning("watch registry sync failed for %s", agent_id, exc_info=True)

    from nautilus_openalgo_bridge.handoff import sync_watch_spec_to_handoff

    handoff = sync_watch_spec_to_handoff(agent_id, watch_spec)
    if profile.uses_nautilus_handoff:
        try:
            from nautilus_openalgo_bridge.reconcile import sync_handoff_from_position_book

            sync_handoff_from_position_book(agent_id, underlying=str(agent.get("symbols", ["NIFTY"])[0]))
        except Exception:
            pass

    latest = get_agent(agent_id) or agent
    latest.pop("watch_spec_pending_activation", None)
    save_agent(latest)
    return handoff


def mcp_propose(**kwargs: Any) -> dict[str, Any]:
    return propose_autonomous_agent(**kwargs)


def mcp_get_status(agent_id: str | None = None) -> dict[str, Any]:
    if agent_id:
        agent = get_agent(agent_id)
        if not agent:
            return {"status": "error", "error": f"agent not found: {agent_id}"}
        authority = load_openalgo_authority(agent=agent)
        if authority.market_context is not None:
            profile = resolve_profile_from_context(agent=agent, market_context=authority.market_context)
        else:
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
        from trade_integrations.autonomous_agents.runtime_status import build_agent_runtime

        runtime = build_agent_runtime(agent, authority=authority)
        paper_status = get_agent_execution_status(agent_id=agent_id, agent=agent, authority=authority)
        execution_context = runtime.get("execution_context") or {}
        session = dict(paper_status.get("session") or {})
        return {
            "status": "ok",
            "agent": agent,
            "execution_profile": profile.prompt_fragment_id,
            "execution_market": execution_context.get("market_region") or profile.market,
            "execution_backend": profile.backend,
            "execution_mode": "paper" if execution_context.get("paper") else profile.mode,
            "paper_session_active": bool((agent.get("status") or "") == "running")
            if profile.uses_openalgo_paper
            else None,
            "paper_session": session if profile.uses_openalgo_paper else None,
            "paper_note": (
                "US agent — OpenAlgo Alpaca plugin is execution authority; INR sandbox P&L may not apply."
                if profile.is_us
                else None
            ),
            "mandate_config": agent.get("mandate_config") or paper_status.get("mandate_config"),
            "bridge_status": bridge_status,
            "watch_path": runtime.get("watch_path"),
            "scheduler_health": runtime.get("scheduler_health"),
            "market_open": runtime.get("market_open"),
            "lifecycle": agent.get("lifecycle") or paper_status.get("lifecycle"),
            "execution_context": runtime.get("execution_context"),
            "analyze_mode": runtime.get("analyze_mode"),
        }
    return {"status": "ok", "agents": list_agents(), "paper_status": get_agent_execution_status()}


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
    stop: float | None = None,
    target: float | None = None,
    spot: float | None = None,
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
    if stop is not None:
        thesis["stop"] = stop
    if target is not None:
        thesis["target"] = target
    if spot is not None:
        thesis["spot"] = spot
    agent["thesis"] = thesis


def _attach_decision_metadata(
    entry: dict[str, Any] | None,
    *,
    confidence: int | None,
    direction: str | None,
    strategy: str | None,
    stop: float | None = None,
    target: float | None = None,
    spot: float | None = None,
) -> dict[str, Any] | None:
    if not entry:
        return entry
    if confidence is not None:
        entry["confidence"] = confidence
    if direction:
        entry["direction"] = direction.strip()
    if strategy:
        entry["strategy"] = strategy.strip()
    if stop is not None:
        entry["stop"] = stop
    if target is not None:
        entry["target"] = target
    if spot is not None:
        entry["spot"] = spot
    return entry


def _record_sim_eval_decision(*, agent_id: str, decision: dict[str, Any]) -> None:
    try:
        from trade_integrations.stock_simulator.integration import is_simulator_active
        from trade_integrations.stock_simulator.sim_runs import record_decision

        if is_simulator_active():
            record_decision(agent_id=agent_id, decision=decision)
    except Exception:
        pass


def _apply_revision_watch_sync(
    agent: dict[str, Any],
    *,
    decision: str,
    strategy: str | None,
    stop: float | None,
    target: float | None,
    spot: float | None,
) -> dict[str, Any]:
    from trade_integrations.autonomous_agents.revision_watch_spec import maybe_sync_watch_spec_on_revision

    return maybe_sync_watch_spec_on_revision(
        agent,
        decision=decision,
        strategy=strategy,
        stop=stop,
        target=target,
        spot=spot,
    )


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
    stop: float | None = None,
    target: float | None = None,
    spot: float | None = None,
    pnl_inr: float | None = None,
    append_outcome: bool = True,
) -> dict[str, Any]:
    agent = get_agent(agent_id)
    if not agent:
        return {"status": "error", "error": f"agent not found: {agent_id}"}
    if str(agent.get("status") or "") not in ("running",):
        return {"status": "error", "error": f"agent not running: {agent.get('status')}"}

    norm_confidence = _normalize_confidence(confidence)
    profile = resolve_profile(agent=agent)
    result = record_agent_decision(
        agent,
        decision=decision,
        rationale=rationale,
        ticker=ticker,
        actions_taken=actions_taken,
        confidence=norm_confidence,
        direction=direction,
        strategy=strategy,
        append_outcome=append_outcome,
    )
    agent = load_agent(agent_id) or agent
    last = dict(result.get("decision") or {})
    _attach_decision_metadata(
        last,
        confidence=norm_confidence,
        direction=direction,
        strategy=strategy,
        stop=stop,
        target=target,
        spot=spot,
    )
    if pnl_inr is not None:
        last["pnl_inr"] = float(pnl_inr)
    if not profile.uses_openalgo_paper:
        last["execution_market"] = profile.market
    agent["last_decision"] = last
    _merge_thesis_from_decision(
        agent,
        decision=decision,
        rationale=rationale,
        confidence=norm_confidence,
        direction=direction,
        strategy=strategy,
        stop=stop,
        target=target,
        spot=spot,
    )

    decision_upper = str(decision).strip().upper()
    watch_sync: dict[str, Any] = {}
    if decision_upper in {"REVISE", "ADJUST"}:
        agent["last_revision_at"] = last.get("at")
        watch_sync = _apply_revision_watch_sync(
            agent,
            decision=decision,
            strategy=strategy,
            stop=stop,
            target=target,
            spot=spot,
        )
    if decision_upper == "EXIT":
        from nautilus_openalgo_bridge.handoff import clear_agent_position_state
        from trade_integrations.autonomous_agents.agent_learning import apply_exit_learning

        if profile.uses_nautilus_handoff:
            clear_agent_position_state(agent_id)
        apply_exit_learning(agent, decision_entry=last)
    elif decision_upper in {"ENTER", "REVISE", "ADJUST"} and profile.uses_nautilus_handoff:
        try:
            from nautilus_openalgo_bridge.reconcile import sync_handoff_from_position_book

            sync_handoff_from_position_book(agent_id, underlying=ticker)
        except Exception:
            pass
    else:
        from trade_integrations.autonomous_agents.agent_learning import sync_agent_thesis_from_lifecycle

        sync_agent_thesis_from_lifecycle(agent)

    save_agent(agent)
    from trade_integrations.autonomous_agents.bootstrap import safe_finalize_bootstrap_if_ready

    safe_finalize_bootstrap_if_ready(agent_id)
    _record_sim_eval_decision(agent_id=agent_id, decision=last)
    response: dict[str, Any] = {
        "status": "ok",
        "agent_id": agent_id,
        **{k: v for k, v in result.items() if k != "status"},
        "thesis": agent.get("thesis"),
        "watch_spec_sync": watch_sync or None,
    }
    if not profile.uses_openalgo_paper:
        response["paper_note"] = (
            f"{profile.market} agent — decision stored on agent record (not OpenAlgo session)."
        )
    return response


def _coerce_watch_spec_dict(value: object) -> dict[str, Any] | None:
    """Normalize watch_spec whether passed as dict or JSON string."""
    if value is None:
        return None
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        parsed = json.loads(text)
        if not isinstance(parsed, dict):
            raise ValueError("watch_spec JSON must be an object")
        return parsed
    raise ValueError(f"watch_spec must be dict or JSON string, got {type(value).__name__}")


def _has_explicit_watch_rules(watch_spec: dict[str, Any] | None) -> bool:
    if not watch_spec:
        return False
    from trade_integrations.autonomous_agents.bootstrap import _coerce_watch_rules

    return bool(_coerce_watch_rules(watch_spec.get("rules")))


def _apply_watch_spec_scalar_overrides(
    watch_spec: dict[str, Any],
    *,
    spot_move_pct: float | None = None,
    cooldown_sec: int | None = None,
    skip_if_unchanged_minutes: int | None = None,
) -> dict[str, Any]:
    if spot_move_pct is not None:
        from trade_integrations.autonomous_agents.bootstrap import _coerce_watch_rules

        coerced = _coerce_watch_rules(watch_spec.get("rules"))
        for rule in coerced:
            if rule.get("metric") == "spot_move_pct":
                rule["threshold"] = float(spot_move_pct)
        if coerced:
            watch_spec["rules"] = coerced
    if cooldown_sec is not None:
        watch_spec["cooldown_sec"] = int(cooldown_sec)
    if skip_if_unchanged_minutes is not None:
        gate = dict(watch_spec.get("gate") or {})
        gate["skip_if_unchanged_minutes"] = int(skip_if_unchanged_minutes)
        watch_spec["gate"] = gate
    return watch_spec


def mcp_set_watch_spec(
    agent_id: str,
    watch_spec: dict[str, Any] | str | None = None,
    *,
    strategy: str | None = None,
    spot: float | None = None,
    target: float | None = None,
    stop: float | None = None,
    spot_move_pct: float | None = None,
    cooldown_sec: int | None = None,
    skip_if_unchanged_minutes: int | None = None,
) -> dict[str, Any]:
    from datetime import datetime, timezone

    try:
        watch_spec = _coerce_watch_spec_dict(watch_spec)
    except (ValueError, json.JSONDecodeError) as exc:
        return {"status": "error", "error": f"watch_spec JSON invalid or truncated: {exc}"}

    agent = get_agent(agent_id)
    if not agent:
        return {"status": "error", "error": f"agent not found: {agent_id}"}
    profile = resolve_profile(agent=agent)

    from trade_integrations.autonomous_agents.watch_compiler import agent_has_user_watch_conditions

    explicit_rules = _has_explicit_watch_rules(watch_spec)
    strategy_name = None
    if not explicit_rules and not agent_has_user_watch_conditions(agent):
        strategy_name = strategy or (watch_spec or {}).get("strategy") or (agent.get("thesis") or {}).get("strategy")
    elif not explicit_rules and agent_has_user_watch_conditions(agent):
        from trade_integrations.autonomous_agents.intent_schema import AgentIntent
        from trade_integrations.autonomous_agents.intent_store import load_intent_from_agent
        from trade_integrations.autonomous_agents.watch_compiler import compile_watch_from_intent

        intent = load_intent_from_agent(agent)
        if intent:
            _, watch_spec = compile_watch_from_intent(
                intent,
                symbols=list(agent.get("symbols") or ["NIFTY"]),
                spot=spot,
            )
            explicit_rules = _has_explicit_watch_rules(watch_spec)

    if strategy_name and not explicit_rules:
        from trade_integrations.autonomous_agents.mandate import mandate_config_from_agent
        from trade_integrations.autonomous_agents.strategy_watch_spec import (
            build_watch_spec_for_strategy,
            format_watch_spec_summary,
        )

        mc = mandate_config_from_agent(agent)
        if spot_move_pct is not None:
            mc.alert_rules.spot_move_pct = float(spot_move_pct)
        else:
            effective_spot = spot
            if effective_spot is None:
                for src in (agent.get("last_decision") or {}, agent.get("thesis") or {}):
                    raw = src.get("spot") if isinstance(src, dict) else None
                    if raw is not None:
                        try:
                            effective_spot = float(raw)
                            break
                        except (TypeError, ValueError):
                            continue
            if mc.alert_rules.spot_move_points and effective_spot and float(effective_spot) > 0:
                mc.alert_rules.spot_move_pct = (
                    float(mc.alert_rules.spot_move_points) / float(effective_spot)
                ) * 100.0
        if cooldown_sec is not None:
            mc.watch_spec["cooldown_sec"] = int(cooldown_sec)
        if skip_if_unchanged_minutes is not None:
            gate = dict(mc.watch_spec.get("gate") or {})
            gate["skip_if_unchanged_minutes"] = int(skip_if_unchanged_minutes)
            mc.watch_spec["gate"] = gate

        symbols = list(agent.get("symbols") or ["NIFTY"])
        watch_spec = build_watch_spec_for_strategy(
            strategy=str(strategy_name),
            mandate=mc,
            symbols=symbols,
            spot=spot,
            target=target,
            stop=stop,
        )
        watch_spec = _apply_watch_spec_scalar_overrides(
            watch_spec,
            spot_move_pct=spot_move_pct,
            cooldown_sec=cooldown_sec,
            skip_if_unchanged_minutes=skip_if_unchanged_minutes,
        )
    elif not watch_spec:
        return {"status": "error", "error": "provide strategy name or explicit watch_spec"}
    elif not explicit_rules:
        return {"status": "error", "error": "provide strategy name or explicit watch_spec"}
    else:
        watch_spec = _apply_watch_spec_scalar_overrides(
            watch_spec,
            spot_move_pct=spot_move_pct,
            cooldown_sec=cooldown_sec,
            skip_if_unchanged_minutes=skip_if_unchanged_minutes,
        )

    agent["watch_spec"] = watch_spec
    agent["watch_spec_updated_at"] = datetime.now(timezone.utc).isoformat()
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
    pending_activation = False
    from trade_integrations.autonomous_agents.plan_approval import is_plan_approved

    agent = get_agent(agent_id) or agent
    if is_plan_approved(agent):
        handoff = activate_watch_spec_for_agent(agent_id, agent, watch_spec, profile=profile)
        _maybe_post_watchers_system_message(agent, summary)
    else:
        agent = get_agent(agent_id) or agent
        agent["watch_spec_pending_activation"] = True
        pending_activation = True
        save_agent(agent)

    return {
        "status": "ok",
        "agent_id": agent_id,
        "watch_spec": watch_spec,
        "watch_summary": summary,
        "handoff_synced": handoff is not None,
        "watch_spec_pending_activation": pending_activation,
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
        from trade_integrations.autonomous_agents.mandate import mandate_config_from_agent

        widget = load_trade_widget(widget_id)
        if not widget:
            return {"status": "error", "error": f"widget not found: {widget_id}"}
        mc = mandate_config_from_agent(agent)
        legs = [ExecutionLeg.from_dict(row) for row in legs_from_widget(widget, product=mc.resolve_product())]

    from nautilus_openalgo_bridge.agent_scoping import strategy_tag_for_agent

    intent = ExecutionIntent(
        action=intent_action,
        agent_id=agent_id,
        rationale=rationale,
        legs=legs,
        widget_id=widget_id,
        underlying=(underlying or (agent.get("symbols") or ["NIFTY"])[0]).upper(),
        strategy=strategy_tag_for_agent(agent_id),
    )
    path = submit_intent(intent)
    results = process_pending_intents(max_count=1)
    return {"status": "submitted", "path": str(path), "results": results}


def mcp_list_watches(
    *,
    session_id: str | None = None,
    agent_id: str | None = None,
) -> dict[str, Any]:
    from trade_integrations.watch_registry.api import mcp_list_watches as _list

    return _list(session_id=session_id, agent_id=agent_id)


def mcp_create_session_watch(
    session_id: str,
    watch_spec: dict[str, Any],
    *,
    symbols: list[str] | None = None,
    label: str | None = None,
    one_shot: bool = False,
) -> dict[str, Any]:
    from trade_integrations.watch_registry.api import mcp_create_watch

    return mcp_create_watch(
        owner_kind="session",
        owner_id=session_id,
        vibe_session_id=session_id,
        watch_spec=watch_spec,
        symbols=symbols,
        label=label,
        one_shot=one_shot,
    )


def mcp_delete_watch(watch_id: str) -> dict[str, Any]:
    from trade_integrations.watch_registry.api import mcp_delete_watch as _delete

    return _delete(watch_id)


def mcp_stop_running_agents(*, unregister_scheduler: bool = True) -> dict[str, Any]:
    from trade_integrations.autonomous_agents.audit import write_agent_audit
    from trade_integrations.autonomous_agents.proposals import stop_autonomous_agent
    from trade_integrations.autonomous_agents.scheduler_cleanup import (
        remove_agent_scheduler_jobs,
        remove_obsolete_scheduler_jobs,
    )

    running_ids = [
        str(row.get("id") or "")
        for row in list_agents()
        if str(row.get("status") or "") == "running" and str(row.get("id") or "")
    ]
    stopped_agents: list[str] = []
    agent_jobs_removed: dict[str, dict[str, bool]] = {}
    stop_errors: list[dict[str, str]] = []

    for agent_id in running_ids:
        try:
            stop_autonomous_agent(agent_id)
            stopped_agents.append(agent_id)
            agent_jobs_removed[agent_id] = remove_agent_scheduler_jobs(agent_id)
        except ValueError as exc:
            stop_errors.append({"agent_id": agent_id, "error": str(exc)})
        except Exception as exc:
            stop_errors.append({"agent_id": agent_id, "error": str(exc)})
            logger.warning("stop_autonomous_agent failed for %s: %s", agent_id, exc)

    scheduler_removed: dict[str, bool] = {}
    if unregister_scheduler:
        try:
            scheduler_removed = remove_obsolete_scheduler_jobs()
        except Exception:
            logger.debug("obsolete scheduler job cleanup skipped", exc_info=True)

    top_status = "stopped"
    if stop_errors and stopped_agents:
        top_status = "partial"
    elif stop_errors and not stopped_agents:
        top_status = "error"

    audit = write_agent_audit(
        "session_stopped",
        detail={
            "stopped_agents": stopped_agents,
            "agent_jobs_removed": agent_jobs_removed,
            "scheduler_removed": scheduler_removed,
            "stop_errors": stop_errors,
        },
    )
    return {
        "status": top_status,
        "stopped_agents": stopped_agents,
        "agent_jobs_removed": agent_jobs_removed,
        "scheduler_removed": scheduler_removed,
        "stop_errors": stop_errors,
        "audit": {"agent_audit": audit, "audit_id": audit.get("audit_id")},
    }


def mcp_get_market_feedback(*, agent_id: str | None = None, ticker: str | None = None) -> dict[str, Any]:
    from trade_integrations.autonomous_agents.execution_actions import get_market_feedback as _get

    resolved_id = agent_id
    if not resolved_id:
        running = [a for a in list_agents() if str(a.get("status") or "") == "running"]
        if len(running) == 1:
            resolved_id = str(running[0].get("id") or "")
    return _get(ticker=ticker, agent_id=resolved_id)


def mcp_execute_basket(
    widget_id: str,
    *,
    agent_id: str | None = None,
    confidence: int | None = None,
) -> dict[str, Any]:
    from trade_integrations.autonomous_agents.execution_actions import execute_basket as _exec

    resolved_id = agent_id
    if not resolved_id:
        running = [a for a in list_agents() if str(a.get("status") or "") == "running"]
        if len(running) == 1:
            resolved_id = str(running[0].get("id") or "")
    return _exec(widget_id, confidence=confidence, agent_id=resolved_id)
