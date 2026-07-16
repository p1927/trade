"""Shared actions invoked by OpenAlgo MCP auto-paper tools."""

from __future__ import annotations

import json
import logging
from dataclasses import asdict
from datetime import datetime, timezone
from typing import Any

from trade_integrations.auto_paper.agent_mandate import DEFAULT_GOAL, DEFAULT_MANDATE, session_summary_for_status
from trade_integrations.auto_paper.audit import write_paper_action
from trade_integrations.auto_paper.config import get_auto_paper_config
from trade_integrations.auto_paper.engine import is_market_session_open
from trade_integrations.auto_paper.market_feedback import build_market_feedback
from trade_integrations.auto_paper.openalgo_client import OpenAlgoClient
from trade_integrations.auto_paper.reconcile import reconcile_paper_state
from trade_integrations.auto_paper.scheduler_cleanup import remove_auto_paper_scheduler_jobs
from trade_integrations.auto_paper.mandate_config import MandateConfig, mandate_config_from_session
from trade_integrations.auto_paper.mandate_enforcer import (
    MandateViolation,
    assert_can_execute,
    product_for_session,
    validate_decision,
)
from trade_integrations.auto_paper.lifecycle import default_lifecycle, on_basket_executed, on_decision
from trade_integrations.auto_paper.outcome_ledger import append_outcome
from trade_integrations.auto_paper.session_store import load_session, save_session, set_vibe_session_id, start_session, stop_session
from trade_integrations.monitor.execution_ledger import list_open_entries, list_open_entries_live, record_execution_from_widget
from trade_integrations.trade_widgets.store import ensure_trade_widget, load_trade_widget

logger = logging.getLogger(__name__)


def _load_widget(widget_id: str) -> dict[str, Any] | None:
    return load_trade_widget(widget_id)


def _orders_from_widget(widget: dict[str, Any], *, product: str) -> list[dict[str, Any]]:
    for step in widget.get("implementation_steps") or []:
        if step.get("action") != "execute_basket":
            continue
        orders = (step.get("payload") or {}).get("orders") or []
        normalized: list[dict[str, Any]] = []
        for order in orders:
            if not isinstance(order, dict) or not order.get("symbol"):
                continue
            row = dict(order)
            row["product"] = product
            normalized.append(row)
        return normalized
    return []


def _audit_payload(record: dict[str, Any]) -> dict[str, Any]:
    """Embed audit record in MCP tool result for Vibe SSE relay."""
    return {"paper_action": record, "audit_id": record.get("audit_id")}


def start_auto_paper(
    *,
    ticker: str,
    budget_inr: float = 20_000.0,
    watchlist: list[str] | None = None,
    max_daily_loss_inr: float = 2_000.0,
    goal: str | None = None,
    mandate: str | None = None,
    agent_mode: bool = True,
    vibe_session_id: str | None = None,
    mandate_config: dict[str, Any] | None = None,
    autonomous_agent_id: str | None = None,
    nautilus_bridge_mode: bool | None = None,
) -> dict[str, Any]:
    symbol = ticker.strip().upper()
    symbols = watchlist or [symbol]
    symbols = [s.strip().upper() for s in symbols if s.strip()]
    if symbol not in symbols:
        symbols.insert(0, symbol)

    client = OpenAlgoClient()
    client.ensure_analyzer_mode()

    session = start_session(
        budget_inr=budget_inr,
        watchlist=symbols,
        autonomous_agent_id=autonomous_agent_id,
    )
    session["agent_mode"] = agent_mode
    session["autonomous"] = True
    session["goal"] = goal or DEFAULT_GOAL
    session["mandate"] = mandate or DEFAULT_MANDATE
    session["max_daily_loss_inr"] = max_daily_loss_inr
    session["primary_ticker"] = symbol
    session["lifecycle"] = default_lifecycle()
    if mandate_config:
        session["mandate_config"] = mandate_config
    if autonomous_agent_id:
        session["autonomous_agent_id"] = autonomous_agent_id.strip()
    if nautilus_bridge_mode:
        session["nautilus_bridge_mode"] = True
    if vibe_session_id:
        session["vibe_session_id"] = vibe_session_id.strip()
        set_vibe_session_id(vibe_session_id.strip())
    save_session(session)

    if nautilus_bridge_mode is None and autonomous_agent_id:
        from trade_integrations.execution.enforce import is_bridge_autonomous_agent

        nautilus_bridge_mode = is_bridge_autonomous_agent(autonomous_agent_id)

    audit = write_paper_action(
        "session_started",
        detail={
            "ticker": symbol,
            "budget_inr": budget_inr,
            "watchlist": symbols,
            "nautilus_bridge_mode": bool(nautilus_bridge_mode),
        },
    )
    if nautilus_bridge_mode and autonomous_agent_id:
        from trade_integrations.execution.bridge_intent import build_bridge_market_feedback

        feedback = build_bridge_market_feedback(agent_id=autonomous_agent_id, ticker=symbol)
    else:
        feedback = build_market_feedback(ticker=symbol)

    research_jobs: dict[str, bool] = {}
    if not nautilus_bridge_mode:
        try:
            from src.scheduled_research.auto_paper_jobs import ensure_vibe_research_jobs, register_mandate_scheduler_jobs

            research_jobs = ensure_vibe_research_jobs()
            mc = MandateConfig.from_dict(session.get("mandate_config"))
            from src.scheduled_research.store import ScheduledResearchJobStore

            register_mandate_scheduler_jobs(ScheduledResearchJobStore(), mc)
        except ImportError:
            pass
        except Exception:
            logger.debug("vibe research jobs registration skipped", exc_info=True)

    mc = mandate_config_from_session(session)
    return {
        "status": "started",
        "paper_mode": True,
        "autonomous": True,
        "primary_ticker": symbol,
        "watchlist": symbols,
        "budget_inr": budget_inr,
        "agent_mode": agent_mode,
        "goal": session["goal"],
        "mandate_config": mc.to_dict(),
        "market_feedback": feedback,
        "vibe_research_jobs": research_jobs,
        "audit": _audit_payload(audit),
        "nautilus_bridge_mode": bool(nautilus_bridge_mode),
        "next_step": (
            "India bridge mode: Nautilus watches via OpenAlgo feed; alerts trigger Vibe turns; "
            "execute via submit_bridge_execution_intent / execute_auto_paper_basket (bridge path)."
            if nautilus_bridge_mode
            else (
                "Autonomous paper session started. Follow your mandate_config rules; "
                "record_auto_paper_decision every turn (ENTER/REVISE/EXIT/HOLD/SKIP)."
            )
        ),
    }


def stop_auto_paper(*, unregister_scheduler: bool = True) -> dict[str, Any]:
    session = stop_session()
    scheduler_removed: dict[str, bool] = {}
    if unregister_scheduler:
        try:
            scheduler_removed = remove_auto_paper_scheduler_jobs()
        except Exception:
            logger.debug("scheduler job cleanup skipped", exc_info=True)
    audit = write_paper_action(
        "session_stopped",
        detail={"stopped_at": session.get("stopped_at"), "scheduler_removed": scheduler_removed},
    )
    return {
        "status": "stopped",
        "stopped_at": session.get("stopped_at"),
        "scheduler_removed": scheduler_removed,
        "audit": _audit_payload(audit),
    }


def get_market_feedback(*, ticker: str | None = None) -> dict[str, Any]:
    session = load_session()
    focus = ticker or session.get("primary_ticker")
    agent_id = str(session.get("autonomous_agent_id") or "").strip()
    if agent_id:
        from trade_integrations.execution.enforce import is_bridge_autonomous_agent
        from trade_integrations.execution.bridge_intent import build_bridge_market_feedback

        if is_bridge_autonomous_agent(agent_id):
            feedback = build_bridge_market_feedback(agent_id=agent_id, ticker=focus)
            audit = write_paper_action("market_feedback", detail={"summary": feedback.get("summary"), "source": "bridge"})
            feedback["audit"] = _audit_payload(audit)
            return feedback
    feedback = build_market_feedback(ticker=focus)
    audit = write_paper_action("market_feedback", detail={"summary": feedback.get("summary")})
    feedback["audit"] = _audit_payload(audit)
    return feedback


def get_status(*, autonomous_agent_id: str | None = None) -> dict[str, Any]:
    cfg = get_auto_paper_config()
    session = load_session(autonomous_agent_id=autonomous_agent_id)
    open_entries = list_open_entries_live()

    funds: dict[str, Any] | None = None
    analyze_mode: bool | None = None
    try:
        client = OpenAlgoClient()
        funds = client.get_funds()
        analyze_mode = client.analyzer_status()
    except RuntimeError:
        pass

    position_summary = []
    for entry in open_entries:
        position_summary.append(
            {
                "widget_id": entry.get("widget_id"),
                "underlying": entry.get("underlying"),
                "recommended_name": entry.get("recommended_name"),
                "execution_mode": entry.get("execution_mode"),
                "net_max_loss": entry.get("net_max_loss"),
            }
        )

    reconcile = reconcile_paper_state()
    mc = mandate_config_from_session(session)
    scheduler_health = _scheduler_health(session)

    return {
        "session": session_summary_for_status(),
        "mandate_config": mc.to_dict(),
        "scheduler_health": scheduler_health,
        "market_open": is_market_session_open(cfg),
        "market_window": f"{cfg.market_open}-{cfg.market_close} IST",
        "open_positions": len(open_entries),
        "positions": position_summary,
        "funds": funds,
        "analyze_mode": analyze_mode,
        "halted": bool(session.get("halted")),
        "halt_reason": session.get("halt_reason"),
        "last_tick_at": session.get("last_tick_at"),
        "last_tick": session.get("last_tick"),
        "last_market_feedback": session.get("last_market_feedback"),
        "reconcile": asdict(reconcile),
    }


def _scheduler_health(session: dict[str, Any]) -> str:
    """ok | stale | disabled — distinct from HTTP stack health."""
    if not session.get("enabled"):
        return "disabled"
    last = session.get("last_agent_turn_at")
    if not last:
        return "stale"
    try:
        last_dt = datetime.fromisoformat(str(last).replace("Z", "+00:00"))
        age_min = (datetime.now(timezone.utc) - last_dt).total_seconds() / 60.0
        cfg = get_auto_paper_config()
        stale_after = max(10.0, (cfg.poll_interval_ms or 300_000) / 60_000 * 2)
        return "ok" if age_min <= stale_after else "stale"
    except ValueError:
        return "stale"


def _write_position_handoff(session: dict[str, Any], widget: dict[str, Any], widget_id: str) -> None:
    agent_id = str(session.get("autonomous_agent_id") or "").strip()
    if not agent_id:
        return
    try:
        from nautilus_openalgo_bridge.handoff import save_handoff
        from nautilus_openalgo_bridge.models import ExecutionLeg, PositionHandoff, StopRules, WatchSpec

        mc = mandate_config_from_session(session)
        legs: list[ExecutionLeg] = []
        for step in widget.get("implementation_steps") or []:
            if step.get("action") != "execute_basket":
                continue
            for order in (step.get("payload") or {}).get("orders") or []:
                if not isinstance(order, dict):
                    continue
                legs.append(
                    ExecutionLeg.from_dict(
                        {
                            "symbol": order.get("symbol"),
                            "exchange": order.get("exchange", "NFO"),
                            "action": order.get("action") or order.get("side"),
                            "quantity": order.get("quantity") or order.get("qty"),
                            "product": order.get("product") or mc.resolve_product(),
                        }
                    )
                )
        underlying = str(widget.get("underlying") or session.get("primary_ticker") or "NIFTY")
        entry_spot = float((widget.get("recommended") or {}).get("spot") or 0)
        if entry_spot <= 0:
            try:
                from nautilus_openalgo_bridge.data_feed import OpenAlgoQuoteFeed

                quotes = OpenAlgoQuoteFeed().poll(symbols=[underlying])
                snap = quotes.get(underlying.upper())
                if snap is not None:
                    entry_spot = snap.ltp
            except Exception:
                logger.debug("entry_spot quote lookup skipped", exc_info=True)

        handoff = PositionHandoff(
            agent_id=agent_id,
            widget_id=widget_id,
            underlying=underlying,
            legs=legs,
            entry_spot=entry_spot,
            watch_spec=WatchSpec.from_dict(mc.watch_spec),
            stop_rules=StopRules(
                max_loss_inr=float(session.get("max_daily_loss_inr") or 2_000) * 0.75,
                flatten_at_close=mc.needs_session_close_flatten(),
            ),
            vibe_session_id=session.get("vibe_session_id"),
        )
        save_handoff(handoff)
        from nautilus_openalgo_bridge.handoff import update_agent_thesis_from_handoff

        update_agent_thesis_from_handoff(handoff)
    except Exception:
        logger.debug("position handoff skipped", exc_info=True)


def execute_basket(widget_id: str, *, confidence: int | None = None) -> dict[str, Any]:
    try:
        widget = ensure_trade_widget(widget_id)
    except ValueError as exc:
        raise ValueError(str(exc)) from exc

    resolved_id = str(widget.get("widget_id") or widget_id)
    if resolved_id != widget_id:
        logger.info("execute_basket: rebuilt widget %s (requested %s)", resolved_id, widget_id)
        widget_id = resolved_id

    cfg = get_auto_paper_config()
    session = load_session()
    mandate = mandate_config_from_session(session)
    try:
        assert_can_execute(session, cfg=cfg, confidence=confidence)
        assert_widget_allowed(widget, mandate)
    except MandateViolation as exc:
        raise ValueError(str(exc)) from exc

    product = product_for_session(session, cfg=cfg)
    agent_id = str(session.get("autonomous_agent_id") or "").strip()
    if agent_id:
        from trade_integrations.execution.enforce import is_bridge_autonomous_agent
        from trade_integrations.execution.bridge_intent import execute_widget_via_bridge

        if is_bridge_autonomous_agent(agent_id):
            bridge_result = execute_widget_via_bridge(
                widget,
                widget_id,
                agent_id=agent_id,
                confidence=confidence,
            )
            audit = write_paper_action(
                "basket_executed",
                detail={
                    "widget_id": widget_id,
                    "underlying": widget.get("underlying"),
                    "strategy": (widget.get("recommended") or {}).get("name"),
                    "execution_path": "nautilus_openalgo_bridge",
                },
            )
            bridge_result["audit"] = _audit_payload(audit)
            return bridge_result

    orders = _orders_from_widget(widget, product=product)
    if not orders:
        raise ValueError(f"No execute_basket orders in widget {widget_id}")

    client = OpenAlgoClient()
    if not client.ensure_analyzer_mode():
        raise RuntimeError("Could not enable OpenAlgo analyzer (paper) mode")

    results = client.place_basket(orders, strategy="auto_paper_agent")
    record_execution_from_widget(widget, results, execution_mode="paper")

    session = load_session()
    session["trades_today"] = int(session.get("trades_today") or 0) + 1
    on_basket_executed(
        session,
        widget_id=widget_id,
        strategy=(widget.get("recommended") or {}).get("name"),
        underlying=widget.get("underlying"),
    )
    save_session(session)
    _write_position_handoff(session, widget, widget_id)

    mc = mandate_config_from_session(session)
    append_outcome(
        symbol=str(widget.get("underlying") or session.get("primary_ticker") or "NIFTY"),
        strategy=(widget.get("recommended") or {}).get("name"),
        action="ENTER",
        intent_source="vibe_basket",
        widget_id=widget_id,
        agent_id=session.get("autonomous_agent_id"),
        mandate_snapshot=mc.to_dict(),
    )

    audit = write_paper_action(
        "basket_executed",
        detail={
            "widget_id": widget_id,
            "underlying": widget.get("underlying"),
            "strategy": (widget.get("recommended") or {}).get("name"),
            "orders": len(orders),
        },
    )

    return {
        "status": "executed",
        "widget_id": widget_id,
        "underlying": widget.get("underlying"),
        "strategy": (widget.get("recommended") or {}).get("name"),
        "orders_placed": len(orders),
        "results": results,
        "execution_mode": "paper",
        "execution_path": "auto_paper_direct",
        "audit": _audit_payload(audit),
    }


def record_decision(
    *,
    decision: str,
    rationale: str,
    ticker: str | None = None,
    actions_taken: list[str] | None = None,
) -> dict[str, Any]:
    session = load_session()
    raw_decision = decision.strip().upper()
    validated, warnings = validate_decision(raw_decision, session)
    entry = {
        "at": datetime.now(timezone.utc).isoformat(),
        "decision": validated,
        "original_decision": raw_decision if validated != raw_decision else None,
        "mandate_warnings": warnings or None,
        "rationale": rationale.strip(),
        "ticker": (ticker or session.get("primary_ticker") or "").strip().upper() or None,
        "actions_taken": actions_taken or [],
    }
    decisions = list(session.get("decisions") or [])
    decisions.append(entry)
    session["decisions"] = decisions[-100:]
    session["last_agent_turn_at"] = entry["at"]
    session["last_decision"] = entry
    on_decision(session, decision=entry["decision"], rationale=entry["rationale"], ticker=entry.get("ticker"))
    if entry["decision"] == "EXIT":
        mc = mandate_config_from_session(session)
        append_outcome(
            symbol=str(entry.get("ticker") or session.get("primary_ticker") or "NIFTY"),
            strategy=(session.get("lifecycle") or {}).get("active_strategy"),
            action="EXIT",
            intent_source="vibe_decision",
            agent_id=session.get("autonomous_agent_id"),
            mandate_snapshot=mc.to_dict(),
        )
    save_session(session)

    audit = write_paper_action("decision_recorded", detail=entry)
    return {"status": "recorded", "decision": entry, "audit": _audit_payload(audit)}


def resume_auto_paper(
    *,
    vibe_session_id: str | None = None,
) -> dict[str, Any]:
    """Resume an active paper session after crash/restart."""
    from trade_integrations.auto_paper.agent_mandate import build_resume_prompt, is_agent_session_active

    session = load_session()
    if not session.get("enabled"):
        return {"status": "inactive", "message": "No active paper session to resume"}

    if vibe_session_id:
        set_vibe_session_id(vibe_session_id.strip())
        session = load_session()

    prompt = build_resume_prompt()
    feedback = build_market_feedback(ticker=session.get("primary_ticker"))
    audit = write_paper_action(
        "session_resumed",
        detail={
            "vibe_session_id": session.get("vibe_session_id"),
            "halted": bool(session.get("halted")),
            "open_positions": len(list_open_entries()),
        },
    )

    return {
        "status": "resumed",
        "session_active": is_agent_session_active(),
        "vibe_session_id": session.get("vibe_session_id"),
        "market_feedback": feedback,
        "resume_prompt": prompt,
        "audit": _audit_payload(audit),
        "next_step": (
            "Send resume_prompt to the Vibe session (POST /trade/auto-paper/resume?dispatch=true). "
            "Agent should get_auto_paper_status, review open positions, then continue trading."
        ),
    }
