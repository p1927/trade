"""OpenAlgo basket execution for autonomous agents (replaces autonomous_agents.mcp_actions.execute_basket)."""

from __future__ import annotations

import logging
from typing import Any

from trade_integrations.autonomous_agents.audit import write_agent_audit
from trade_integrations.autonomous_agents.lifecycle import on_basket_executed, sync_agent_lifecycle_after_basket
from trade_integrations.autonomous_agents.mandate import (
    MandateViolation,
    assert_can_execute,
    assert_widget_allowed,
    mandate_config_from_agent,
    product_for_session,
)
from trade_integrations.autonomous_agents.outcome_ledger import append_outcome
from trade_integrations.autonomous_agents.trading_config import get_agent_trading_config
from trade_integrations.execution.openalgo_client import OpenAlgoClient
from trade_integrations.monitor.execution_ledger import record_execution_from_widget
from trade_integrations.trade_widgets.store import ensure_trade_widget, load_trade_widget

logger = logging.getLogger(__name__)


def _audit_payload(record: dict[str, Any]) -> dict[str, Any]:
    return {"paper_action": record, "audit_id": record.get("audit_id")}


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


def _resolve_agent_for_widget(widget_id: str, agent_id: str | None) -> tuple[dict[str, Any] | None, str | None]:
    if agent_id:
        from trade_integrations.autonomous_agents.store import get_agent

        agent = get_agent(agent_id)
        return agent, agent_id if agent else None
    from trade_integrations.autonomous_agents.store import list_agents

    for row in list_agents():
        if str(row.get("status") or "") != "running":
            continue
        aid = str(row.get("id") or "").strip()
        if aid:
            from trade_integrations.autonomous_agents.store import get_agent

            return get_agent(aid), aid
    return None, None


def _pseudo_session(agent: dict[str, Any]) -> dict[str, Any]:
    mc = mandate_config_from_agent(agent)
    symbols = list(agent.get("symbols") or ["NIFTY"])
    return {
        "mandate_config": mc.to_dict(),
        "primary_ticker": symbols[0],
        "watchlist": symbols,
        "autonomous_agent_id": agent.get("id"),
        "lifecycle": agent.get("lifecycle"),
        "user_guidance": list(agent.get("user_guidance") or []),
    }


def execute_basket(
    widget_id: str,
    *,
    confidence: int | None = None,
    agent_id: str | None = None,
) -> dict[str, Any]:
    try:
        widget = ensure_trade_widget(widget_id)
    except ValueError as exc:
        raise ValueError(str(exc)) from exc

    resolved_id = str(widget.get("widget_id") or widget_id)
    if resolved_id != widget_id:
        logger.info("execute_basket: rebuilt widget %s (requested %s)", resolved_id, widget_id)
        widget_id = resolved_id

    agent, resolved_agent_id = _resolve_agent_for_widget(widget_id, agent_id)
    cfg = get_agent_trading_config()
    session = _pseudo_session(agent) if agent else {"primary_ticker": widget.get("underlying"), "watchlist": []}
    mandate = mandate_config_from_agent(agent) if agent else mandate_config_from_agent({"mandate_config": {}})
    try:
        if agent:
            from trade_integrations.autonomous_agents.plan_approval import assert_plan_approved

            assert_plan_approved(agent)
        assert_can_execute(session, cfg=cfg, confidence=confidence)
        assert_widget_allowed(widget, mandate)
    except MandateViolation as exc:
        raise ValueError(str(exc)) from exc

    if resolved_agent_id:
        from trade_integrations.execution.enforce import is_bridge_autonomous_agent
        from trade_integrations.execution.bridge_intent import execute_widget_via_bridge

        if is_bridge_autonomous_agent(resolved_agent_id):
            bridge_result = execute_widget_via_bridge(
                widget,
                widget_id,
                agent_id=resolved_agent_id,
                confidence=confidence,
            )
            audit = write_agent_audit(
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

    product = product_for_session(session, cfg=cfg)
    orders = _orders_from_widget(widget, product=product)
    if not orders:
        raise ValueError(f"No execute_basket orders in widget {widget_id}")

    client = OpenAlgoClient()
    from trade_integrations.execution.context_verify import ensure_paper_execution_ready
    from trade_integrations.execution.default_profile import paper_mode_env_enabled

    ensure_paper_execution_ready(client, env_paper_lock=paper_mode_env_enabled())

    results = client.place_basket(orders, strategy="autonomous_agent")
    record_execution_from_widget(widget, results, execution_mode="paper")

    if agent and resolved_agent_id:
        sync_agent_lifecycle_after_basket(
            resolved_agent_id,
            widget_id=widget_id,
            strategy=(widget.get("recommended") or {}).get("name"),
            underlying=widget.get("underlying"),
        )

    mc = mandate_config_from_agent(agent) if agent else mandate
    append_outcome(
        symbol=str(widget.get("underlying") or session.get("primary_ticker") or "NIFTY"),
        strategy=(widget.get("recommended") or {}).get("name"),
        action="ENTER",
        intent_source="vibe_basket",
        widget_id=widget_id,
        agent_id=resolved_agent_id,
        mandate_snapshot=mc.to_dict(),
    )

    audit = write_agent_audit(
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
        "execution_path": "autonomous_agent_direct",
        "agent_id": resolved_agent_id,
        "audit": _audit_payload(audit),
    }


def get_market_feedback(*, ticker: str | None = None, agent_id: str | None = None) -> dict[str, Any]:
    from trade_integrations.autonomous_agents.market_feedback import build_market_feedback

    if agent_id:
        feedback = build_market_feedback(ticker=ticker, agent_id=agent_id)
    else:
        feedback = build_market_feedback(ticker=ticker)
    audit = write_agent_audit("market_feedback", detail={"summary": feedback.get("summary")})
    feedback["audit"] = _audit_payload(audit)
    return feedback
