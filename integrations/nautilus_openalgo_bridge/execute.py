"""Translate ExecutionIntent → OpenAlgo REST (sole execution path)."""

from __future__ import annotations

import logging
from typing import Any

from nautilus_openalgo_bridge.config import BridgeConfig, get_bridge_config
from nautilus_openalgo_bridge.intent_queue import archive_intent
from nautilus_openalgo_bridge.models import ExecutionIntent, IntentAction
from nautilus_openalgo_bridge.openalgo_client import BridgeOpenAlgoClient, get_openalgo_client
from nautilus_openalgo_bridge.orders import leg_to_openalgo_order, legs_to_openalgo_orders
from nautilus_openalgo_bridge.preflight import run_preflight
from nautilus_openalgo_bridge.reconcile import (
    open_positions_from_book,
    reconcile_after_intent,
    snapshot_agent_positions,
    total_unrealized_pnl,
)

__all__ = ["execute_intent", "process_intent_file", "leg_to_openalgo_order", "legs_to_openalgo_orders"]

logger = logging.getLogger(__name__)


def _exit_realized_pnl_from_reconcile(
    pre_exit_unrealized: float | None,
    postflight: dict[str, Any],
) -> float | None:
    """Derive realized exit P&L from post-exit reconcile snapshot."""
    open_positions = int(postflight.get("open_positions") or 0)
    post_unrealized = postflight.get("unrealized_pnl_inr")
    if open_positions == 0 and pre_exit_unrealized is not None:
        return pre_exit_unrealized
    if pre_exit_unrealized is not None and post_unrealized is not None:
        return pre_exit_unrealized - post_unrealized
    return None


def execute_intent(
    intent: ExecutionIntent,
    *,
    client: BridgeOpenAlgoClient | None = None,
    config: BridgeConfig | None = None,
    persist: bool = True,
    skip_preflight: bool = False,
) -> dict[str, Any]:
    """Execute a bridge intent via OpenAlgo. Nautilus never calls the broker directly."""
    cfg = config or get_bridge_config()
    oa = client or get_openalgo_client(cfg)
    action = intent.action

    if action == IntentAction.HOLD:
        payload = {
            "status": "skipped",
            "action": action.value,
            "reason": intent.rationale or "hold",
            "agent_id": intent.agent_id,
        }
        if persist:
            archive_intent(intent, payload)
        reconcile_after_intent(intent, client=oa, execution_result=payload)
        return payload

    pre_snapshot = snapshot_agent_positions(str(intent.agent_id or ""), client=oa) if intent.agent_id else None

    if not skip_preflight:
        preflight = run_preflight(intent, oa, cfg)
        if preflight.get("blocked"):
            payload = {
                "status": "blocked",
                "action": action.value,
                "agent_id": intent.agent_id,
                **preflight,
            }
            if persist:
                archive_intent(intent, payload)
            return payload

    strategy = intent.strategy or "nautilus_bridge"

    if action == IntentAction.EXIT:
        from nautilus_openalgo_bridge.agent_scoping import filter_positions_for_agent, resolve_exit_legs_for_agent

        pre_exit_pnl: float | None = None
        try:
            book_rows = oa.get_position_book()
            scoped_pre = (
                filter_positions_for_agent(book_rows, str(intent.agent_id or ""))
                if intent.agent_id
                else open_positions_from_book(book_rows)
            )
            pre_exit_pnl = total_unrealized_pnl(scoped_pre)
        except Exception:
            logger.debug("pre-exit pnl snapshot skipped", exc_info=True)

        underlying = str(intent.underlying or "NIFTY").upper()
        exit_legs = resolve_exit_legs_for_agent(
            agent_id=str(intent.agent_id or ""),
            position_rows=oa.get_position_book(),
            underlying=underlying,
            explicit_legs=list(intent.legs) if intent.legs else None,
        )
        orders = legs_to_openalgo_orders(exit_legs)
        if not orders:
            payload = {
                "status": "blocked",
                "error": "EXIT blocked — no agent-scoped positions (close_all disabled for multi-agent)",
                "agent_id": intent.agent_id,
            }
            if persist:
                archive_intent(intent, payload)
            return payload
        results = oa.place_basket(orders, strategy=strategy)
        payload = {
            "status": "executed",
            "action": action.value,
            "mode": "leg_basket",
            "orders_placed": len(orders),
            "results": results,
            "agent_id": intent.agent_id,
            "intent_id": intent.intent_id,
        }
        if intent.agent_id:
            try:
                from nautilus_openalgo_bridge.handoff import clear_agent_position_state

                clear_agent_position_state(intent.agent_id)
            except Exception:
                logger.debug("handoff clear on EXIT skipped", exc_info=True)
        postflight = reconcile_after_intent(
            intent,
            client=oa,
            execution_result=payload,
            pre_snapshot=pre_snapshot,
        )
        payload["postflight"] = postflight
        realized_pnl = _exit_realized_pnl_from_reconcile(pre_exit_pnl, postflight)
        try:
            from trade_integrations.autonomous_agents.outcome_ledger import append_outcome, reconcile_exit_outcome

            append_outcome(
                symbol=str(intent.underlying or "NIFTY"),
                strategy=intent.strategy,
                action="EXIT",
                intent_source="nautilus_intent",
                agent_id=intent.agent_id or None,
            )
            if realized_pnl is not None:
                reconcile_exit_outcome(
                    symbol=str(intent.underlying or "NIFTY"),
                    strategy=intent.strategy,
                    agent_id=intent.agent_id or None,
                    net_pnl_inr=realized_pnl,
                    intent_source="nautilus_reconcile",
                )
        except Exception:
            logger.debug("outcome ledger append skipped", exc_info=True)
        if persist:
            archive_intent(intent, payload)
        return payload

    if action in (IntentAction.ENTER, IntentAction.ADJUST):
        orders = legs_to_openalgo_orders(intent.legs)
        if not orders:
            payload = {"status": "error", "error": f"{action.value} intent has no valid legs", "agent_id": intent.agent_id}
            if persist:
                archive_intent(intent, payload)
            return payload
        results = oa.place_basket(orders, strategy=strategy)
        payload = {
            "status": "executed",
            "action": action.value,
            "orders_placed": len(orders),
            "results": results,
            "agent_id": intent.agent_id,
            "intent_id": intent.intent_id,
            "underlying": intent.underlying,
            "widget_id": intent.widget_id,
        }
        postflight = reconcile_after_intent(
            intent,
            client=oa,
            execution_result=payload,
            pre_snapshot=pre_snapshot,
        )
        payload["postflight"] = postflight
        if persist:
            archive_intent(intent, payload)
        return payload

    payload = {"status": "error", "error": f"unsupported action: {action}", "agent_id": intent.agent_id}
    if persist:
        archive_intent(intent, payload)
    return payload


def process_intent_file(path: str, **kwargs: Any) -> dict[str, Any]:
    """Load and execute one queued intent JSON file."""
    import json
    from pathlib import Path

    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        intent = ExecutionIntent.from_dict(payload)
    except (OSError, json.JSONDecodeError, ValueError, TypeError) as exc:
        logger.warning("skip invalid intent file %s: %s", path, exc)
        return {"status": "error", "error": str(exc), "path": path}
    result = execute_intent(intent, **kwargs)
    logger.info("intent %s → %s", intent.intent_id or path, result.get("status"))
    return result
