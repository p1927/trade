"""Route India autonomous execution through nautilus_openalgo_bridge → OpenAlgo."""

from __future__ import annotations

import logging
from typing import Any

from trade_integrations.autonomous_agents.mandate import mandate_config_from_agent
from trade_integrations.autonomous_agents.mandate_enforcer import assert_can_execute, assert_widget_allowed, MandateViolation
from trade_integrations.execution.profile import resolve_profile
from trade_integrations.autonomous_agents.store import get_agent

logger = logging.getLogger(__name__)


def _agent_uses_bridge(agent_id: str | None) -> bool:
    if not agent_id:
        return False
    agent = get_agent(agent_id.strip())
    if not agent:
        return False
    return resolve_profile(agent=agent).uses_nautilus_handoff


def legs_from_widget(widget: dict[str, Any], *, product: str) -> list[dict[str, Any]]:
    orders: list[dict[str, Any]] = []
    basket_steps = 0
    for step in widget.get("implementation_steps") or []:
        if step.get("action") != "execute_basket":
            continue
        basket_steps += 1
        for order in (step.get("payload") or {}).get("orders") or []:
            if not isinstance(order, dict) or not order.get("symbol"):
                continue
            row = dict(order)
            row["product"] = product
            orders.append(row)
    if basket_steps > 1:
        raise ValueError(
            f"Widget has {basket_steps} execute_basket steps; expected exactly one"
        )
    return orders


def build_adjust_legs_from_widget(
    *,
    handoff_legs: list,
    widget: dict[str, Any],
    product: str,
) -> list:
    """Build minimal delta legs: close removed/changed legs, open new target legs."""
    from nautilus_openalgo_bridge.models import ExecutionLeg

    target_raw = legs_from_widget(widget, product=product)
    target = [ExecutionLeg.from_dict(row) for row in target_raw]
    if not handoff_legs:
        return target

    def _key(leg) -> tuple[str, str]:
        return (str(leg.symbol).upper(), str(leg.exchange).upper())

    handoff_map = {_key(leg): leg for leg in handoff_legs}
    target_map = {_key(leg): leg for leg in target}
    delta: list[ExecutionLeg] = []

    for key, hleg in handoff_map.items():
        tleg = target_map.get(key)
        if tleg is None or tleg.action != hleg.action or tleg.quantity != hleg.quantity:
            close_action = "BUY" if hleg.action == "SELL" else "SELL"
            delta.append(
                ExecutionLeg(
                    symbol=hleg.symbol,
                    exchange=hleg.exchange,
                    action=close_action,
                    quantity=hleg.quantity,
                    product=hleg.product,
                    order_type=hleg.order_type,
                )
            )

    for key, tleg in target_map.items():
        hleg = handoff_map.get(key)
        if hleg is None or hleg.action != tleg.action or hleg.quantity != tleg.quantity:
            delta.append(tleg)

    return delta


def build_bridge_market_feedback(*, agent_id: str, ticker: str | None = None) -> dict[str, Any]:
    """Read-only status from Nautilus handoff + OpenAlgo position book (no parallel watch rules)."""
    agent = get_agent(agent_id)
    if not agent:
        return {"status": "error", "error": f"agent not found: {agent_id}"}

    focus = ticker or (agent.get("symbols") or ["NIFTY"])[0]
    handoff = None
    quotes: dict[str, Any] = {}
    open_positions: list[dict[str, Any]] = []

    try:
        from nautilus_openalgo_bridge.handoff import load_handoff
        from nautilus_openalgo_bridge.data_feed import OpenAlgoQuoteFeed
        from nautilus_openalgo_bridge.reconcile import open_positions_from_book
        from nautilus_openalgo_bridge.openalgo_client import get_openalgo_client

        handoff = load_handoff(agent_id)
        try:
            quotes = {
                k: v.to_dict()
                for k, v in OpenAlgoQuoteFeed().poll(symbols=[str(focus).upper()]).items()
            }
        except Exception:
            logger.debug("bridge quote poll skipped", exc_info=True)
        try:
            client = get_openalgo_client()
            open_positions = open_positions_from_book(client.get_position_book())
        except Exception:
            logger.debug("position book read skipped", exc_info=True)
    except ImportError as exc:
        return {"status": "error", "error": f"bridge unavailable: {exc}"}

    alerts: list[str] = []
    if not handoff or not handoff.legs:
        alerts.append("no_open_handoff")
    if agent.get("last_bridge_alert"):
        alert = dict(agent.get("last_bridge_alert") or {})
        alerts.append(str(alert.get("message") or alert.get("signal") or "bridge_alert"))

    return {
        "status": "ok",
        "source": "nautilus_openalgo_bridge",
        "focus_ticker": focus,
        "handoff_active": handoff is not None,
        "underlying": handoff.underlying if handoff else focus,
        "open_legs": len(handoff.legs) if handoff else 0,
        "watch_rules": len(handoff.watch_spec.rules) if handoff and handoff.watch_spec else 0,
        "quotes": quotes,
        "open_positions": open_positions,
        "alerts": alerts,
        "requires_action": bool(agent.get("streaming")),
        "summary": (
            f"Bridge watch — {focus} handoff={'active' if handoff else 'none'}, "
            f"{len(open_positions)} open position(s), rules via Nautilus only."
        ),
    }


def execute_widget_via_bridge(
    widget: dict[str, Any],
    widget_id: str,
    *,
    agent_id: str,
    confidence: int | None = None,
    action: str = "ENTER",
    rationale: str = "vibe_basket",
) -> dict[str, Any]:
    """Execute ENTER/ADJUST via bridge execute_intent → OpenAlgo (sole execution path for IN autonomous)."""
    from nautilus_openalgo_bridge.execute import execute_intent
    from nautilus_openalgo_bridge.models import ExecutionIntent, ExecutionLeg, IntentAction

    agent = get_agent(agent_id)
    if not agent:
        raise ValueError(f"agent not found: {agent_id}")
    profile = resolve_profile(agent=agent)
    execution_mode = profile.mode

    mandate = mandate_config_from_agent(agent)
    pseudo_session = {
        "mandate_config": mandate.to_dict(),
        "primary_ticker": (agent.get("symbols") or ["NIFTY"])[0],
        "watchlist": list(agent.get("symbols") or ["NIFTY"]),
        "autonomous_agent_id": agent_id,
        "lifecycle": agent.get("lifecycle"),
        "user_guidance": list(agent.get("user_guidance") or []),
    }
    if profile.uses_nautilus_handoff:
        try:
            assert_widget_allowed(widget, mandate)
        except MandateViolation as exc:
            raise ValueError(str(exc)) from exc
    else:
        try:
            assert_can_execute(
                pseudo_session,
                mandate=mandate,
                confidence=confidence,
                require_active_session=str(agent.get("status") or "") == "running",
            )
            assert_widget_allowed(widget, mandate)
        except MandateViolation as exc:
            raise ValueError(str(exc)) from exc

    product = mandate.resolve_product()
    raw_orders = legs_from_widget(widget, product=product)
    if action.upper() == "ADJUST":
        from nautilus_openalgo_bridge.handoff import load_handoff

        handoff = load_handoff(agent_id)
        if handoff and handoff.legs:
            adjust_legs = build_adjust_legs_from_widget(
                handoff_legs=list(handoff.legs),
                widget=widget,
                product=product,
            )
            if adjust_legs:
                raw_orders = [leg.to_dict() for leg in adjust_legs]
    if not raw_orders:
        raise ValueError(f"No execute_basket orders in widget {widget_id}")

    action_enum = IntentAction.ADJUST if action.upper() == "ADJUST" else IntentAction.ENTER
    legs = [ExecutionLeg.from_dict(row) for row in raw_orders]
    underlying = str(widget.get("underlying") or pseudo_session.get("primary_ticker") or "NIFTY").upper()
    from nautilus_openalgo_bridge.agent_scoping import strategy_tag_for_agent

    display_strategy = str((widget.get("recommended") or {}).get("name") or "vibe_bridge")
    order_strategy = strategy_tag_for_agent(agent_id)

    intent = ExecutionIntent(
        action=action_enum,
        agent_id=agent_id,
        rationale=rationale,
        confidence=int(confidence or 0),
        legs=legs,
        strategy=order_strategy,
        widget_id=widget_id,
        underlying=underlying,
    )

    result = execute_intent(intent, persist=True)
    if result.get("status") not in {"executed", "skipped"}:
        err = result.get("error") or result.get("reason") or result.get("status")
        raise RuntimeError(f"Bridge execution failed: {err}")

    from trade_integrations.monitor.execution_ledger import record_execution_from_widget
    from trade_integrations.autonomous_agents.lifecycle import sync_agent_lifecycle_after_basket
    from trade_integrations.autonomous_agents.outcome_ledger import append_outcome

    record_execution_from_widget(widget, result.get("results") or [result], execution_mode=execution_mode, agent_id=agent_id)
    sync_agent_lifecycle_after_basket(
        agent_id,
        widget_id=widget_id,
        strategy=display_strategy,
        underlying=underlying,
    )
    append_outcome(
        symbol=underlying,
        strategy=display_strategy,
        action=action_enum.value,
        intent_source="nautilus_bridge",
        widget_id=widget_id,
        agent_id=agent_id,
        mandate_snapshot=mandate.to_dict(),
    )

    return {
        "status": result.get("status", "executed"),
        "execution_path": "nautilus_openalgo_bridge",
        "widget_id": widget_id,
        "underlying": underlying,
        "strategy": display_strategy,
        "order_strategy": order_strategy,
        "orders_placed": result.get("orders_placed") or len(legs),
        "results": result.get("results") or result,
        "postflight": result.get("postflight"),
        "execution_mode": execution_mode,
    }


def submit_exit_intent(
    *,
    agent_id: str,
    rationale: str,
    underlying: str | None = None,
) -> dict[str, Any]:
    """Queue EXIT intent for bridge processing (used when Vibe decides to flatten)."""
    from nautilus_openalgo_bridge.agent_scoping import default_exit_underlying
    from nautilus_openalgo_bridge.handoff import load_handoff
    from nautilus_openalgo_bridge.agent_scoping import strategy_tag_for_agent
    from nautilus_openalgo_bridge.intent_queue import submit_intent
    from nautilus_openalgo_bridge.models import ExecutionIntent, IntentAction

    handoff = load_handoff(agent_id)
    intent = ExecutionIntent(
        action=IntentAction.EXIT,
        agent_id=agent_id,
        rationale=rationale,
        underlying=default_exit_underlying(agent_id, explicit=underlying),
        legs=list(handoff.legs) if handoff and handoff.legs else [],
        strategy=strategy_tag_for_agent(agent_id),
    )
    path = submit_intent(intent)
    from nautilus_openalgo_bridge.intent_queue import process_pending_intents

    results = process_pending_intents(max_count=1)
    return {"status": "submitted", "path": str(path), "results": results}
