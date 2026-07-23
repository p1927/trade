"""Reconcile OpenAlgo positionbook with bridge handoff and agent thesis."""

from __future__ import annotations

import logging
from typing import Any

from nautilus_openalgo_bridge.handoff import (
    load_handoff,
    save_handoff,
    update_agent_thesis_from_handoff,
)
from nautilus_openalgo_bridge.instruments import position_rows_to_legs, validate_option_legs
from nautilus_openalgo_bridge.models import BridgeSignal, ExecutionIntent, IntentAction, PositionHandoff, WatchAlert
from nautilus_openalgo_bridge.openalgo_client import BridgeOpenAlgoClient, get_openalgo_client

logger = logging.getLogger(__name__)


def open_positions_from_book(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        try:
            qty = int(float(row.get("quantity") or row.get("netqty") or 0))
        except (TypeError, ValueError):
            qty = 0
        if qty != 0:
            out.append(row)
    return out


def total_unrealized_pnl(rows: list[dict[str, Any]]) -> float | None:
    total = 0.0
    found = False
    for row in rows:
        for key in ("pnl", "unrealised", "unrealized", "m2m"):
            raw = row.get(key)
            if raw is None:
                continue
            try:
                total += float(raw)
                found = True
                break
            except (TypeError, ValueError):
                continue
    return total if found else None


def snapshot_agent_positions(
    agent_id: str,
    *,
    client: BridgeOpenAlgoClient | None = None,
) -> dict[str, Any]:
    """Lightweight pre/post execution position snapshot for one agent."""
    if not agent_id:
        return {"open_positions": 0, "legs": 0}
    oa = client or get_openalgo_client()
    handoff = load_handoff(agent_id)
    rows = oa.get_position_book()
    from nautilus_openalgo_bridge.agent_scoping import filter_positions_for_agent

    open_rows = filter_positions_for_agent(rows, agent_id)
    return {
        "open_positions": len(open_rows),
        "legs": len(handoff.legs) if handoff and handoff.legs else 0,
        "unrealized_pnl_inr": total_unrealized_pnl(open_rows),
    }


def _normalize_order_status(raw: Any) -> str:
    text = str(raw or "").strip().lower()
    if text in {"complete", "completed", "filled", "traded", "success"}:
        return "filled"
    if text in {"rejected", "cancelled", "canceled", "failed"}:
        return "rejected"
    if text in {"open", "pending", "trigger pending", "validation pending"}:
        return "pending"
    if text in {"partially filled", "partial", "partiallyfilled"}:
        return "partial"
    return text or "unknown"


def read_order_state_for_agent(
    agent_id: str,
    *,
    client: BridgeOpenAlgoClient | None = None,
    underlying: str | None = None,
) -> dict[str, Any]:
    """Summarize OpenAlgo orderbook rows scoped to one autonomous agent."""
    if not agent_id:
        return {"orders": [], "filled": 0, "pending": 0, "rejected": 0, "partial": False}
    oa = client or get_openalgo_client()
    try:
        from nautilus_openalgo_bridge.agent_scoping import agent_symbol_universe, strategy_tag_for_agent

        tag = strategy_tag_for_agent(agent_id)
        universe = agent_symbol_universe(agent_id)
        ul = str(underlying or "").upper()
        rows = oa.get_orderbook()
    except Exception as exc:
        return {"orders": [], "error": str(exc), "filled": 0, "pending": 0, "rejected": 0, "partial": False}

    scoped: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        row_tag = str(row.get("strategy") or row.get("tag") or "").strip()
        symbol = str(row.get("symbol") or row.get("tradingsymbol") or "").upper()
        if row_tag and row_tag == tag:
            scoped.append(row)
            continue
        if symbol and symbol in universe:
            scoped.append(row)
            continue
        if ul and symbol.startswith(ul):
            scoped.append(row)

    filled = pending = rejected = 0
    partial = False
    order_rows: list[dict[str, Any]] = []
    for row in scoped[-20:]:
        status = _normalize_order_status(row.get("status") or row.get("order_status"))
        if status == "filled":
            filled += 1
        elif status == "rejected":
            rejected += 1
        elif status == "partial":
            partial = True
            pending += 1
        elif status == "pending":
            pending += 1
        order_rows.append(
            {
                "symbol": row.get("symbol") or row.get("tradingsymbol"),
                "status": status,
                "action": row.get("action") or row.get("transactiontype"),
                "quantity": row.get("quantity") or row.get("qty"),
                "filled_quantity": row.get("filled_quantity") or row.get("filledqty"),
            }
        )

    return {
        "orders": order_rows,
        "filled": filled,
        "pending": pending,
        "rejected": rejected,
        "partial": partial,
    }


def sync_handoff_from_position_book(
    agent_id: str,
    *,
    client: BridgeOpenAlgoClient | None = None,
    underlying: str | None = None,
) -> PositionHandoff | None:
    """Refresh handoff legs from OpenAlgo positionbook (source of truth)."""
    from nautilus_openalgo_bridge.handoff import build_handoff_shell_from_agent
    from trade_integrations.autonomous_agents.store import get_agent

    oa = client or get_openalgo_client()
    handoff = load_handoff(agent_id)
    if handoff is None:
        agent = get_agent(agent_id)
        if not agent:
            return None
        handoff = build_handoff_shell_from_agent(agent)

    rows = oa.get_position_book()
    if agent_id:
        from nautilus_openalgo_bridge.agent_scoping import filter_positions_for_agent

        open_rows = filter_positions_for_agent(rows, agent_id)
    else:
        open_rows = open_positions_from_book(rows)
    ul = underlying or handoff.underlying
    legs = position_rows_to_legs(open_rows, underlying=ul)

    if legs:
        try:
            legs = validate_option_legs(legs, oa)
        except Exception as exc:
            logger.warning("handoff leg validation failed for %s: %s", agent_id, exc)

    handoff.legs = legs
    save_handoff(handoff)
    update_agent_thesis_from_handoff(handoff)
    return handoff


def maybe_reconcile_handoff_mismatch(
    agent_id: str,
    *,
    client: BridgeOpenAlgoClient | None = None,
) -> WatchAlert | None:
    """Sync handoff from positionbook; emit REVIEW_NEEDED when legs still diverge."""
    handoff = load_handoff(agent_id)
    if not handoff or not handoff.legs:
        return None

    oa = client or get_openalgo_client()
    sync_handoff_from_position_book(agent_id, client=oa, underlying=handoff.underlying)
    handoff = load_handoff(agent_id)
    from nautilus_openalgo_bridge.agent_scoping import filter_positions_for_agent

    open_rows = filter_positions_for_agent(oa.get_position_book(), agent_id)
    open_count = len(open_rows)
    leg_count = len(handoff.legs) if handoff and handoff.legs else 0

    if leg_count > 0 and open_count == 0:
        return WatchAlert(
            signal=BridgeSignal.REVIEW_NEEDED,
            rule=None,
            symbol=handoff.underlying,
            message="Handoff legs exist but positionbook is flat — reconcile before REVISE",
        )
    if leg_count != open_count:
        return WatchAlert(
            signal=BridgeSignal.REVIEW_NEEDED,
            rule=None,
            symbol=handoff.underlying,
            message=f"Handoff leg count ({leg_count}) != positionbook rows ({open_count})",
        )
    return None


def reconcile_after_intent(
    intent: ExecutionIntent,
    *,
    client: BridgeOpenAlgoClient | None = None,
    execution_result: dict[str, Any] | None = None,
    pre_snapshot: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Post-flight: positionbook → handoff + agent thesis + decision log."""
    agent_id = str(intent.agent_id or "").strip()
    if not agent_id:
        return {"status": "skipped", "reason": "no_agent_id"}

    oa = client or get_openalgo_client()
    pre = dict(pre_snapshot or snapshot_agent_positions(agent_id, client=oa))
    rows = oa.get_position_book()
    from nautilus_openalgo_bridge.agent_scoping import filter_positions_for_agent

    open_rows = filter_positions_for_agent(rows, agent_id)
    pnl = total_unrealized_pnl(open_rows)
    order_state = read_order_state_for_agent(
        agent_id,
        client=oa,
        underlying=intent.underlying,
    )

    payload: dict[str, Any] = {
        "open_positions": len(open_rows),
        "unrealized_pnl_inr": pnl,
        "order_state": order_state,
        "pre_snapshot": pre,
    }

    handoff = load_handoff(agent_id)
    handoff_leg_count = len(handoff.legs) if handoff and handoff.legs else 0
    payload["legs"] = handoff_leg_count
    if handoff_leg_count > 0 and len(open_rows) == 0:
        payload["handoff_book_mismatch"] = True

    if intent.action == IntentAction.EXIT:
        payload["handoff"] = "cleared_on_exit"
        payload["open_positions"] = len(open_rows)
        payload["unrealized_pnl_inr"] = pnl
        _record_decision_for_intent(
            intent,
            execution_result=execution_result,
            realized_pnl_inr=pre.get("unrealized_pnl_inr"),
        )
        _maybe_schedule_post_execution(intent, payload, pre=pre, order_state=order_state, execution_result=execution_result)
        return payload

    if intent.action in (IntentAction.ENTER, IntentAction.ADJUST):
        handoff = sync_handoff_from_position_book(
            agent_id,
            client=oa,
            underlying=intent.underlying,
        )
        payload["handoff_synced"] = handoff is not None
        if handoff:
            payload["legs"] = len(handoff.legs)
            if len(handoff.legs) > 0 and len(open_rows) == 0:
                payload["handoff_book_mismatch"] = True

    _record_decision_for_intent(intent, execution_result=execution_result)
    _maybe_schedule_post_execution(intent, payload, pre=pre, order_state=order_state, execution_result=execution_result)
    return payload


def _maybe_schedule_post_execution(
    intent: ExecutionIntent,
    payload: dict[str, Any],
    *,
    pre: dict[str, Any],
    order_state: dict[str, Any],
    execution_result: dict[str, Any] | None,
) -> None:
    agent_id = str(intent.agent_id or "").strip()
    if not agent_id:
        return
    try:
        from trade_integrations.autonomous_agents.post_execution import schedule_post_execution_turn

        schedule_post_execution_turn(
            agent_id,
            intent_action=intent.action.value,
            reconcile_payload=payload,
            pre_snapshot=pre,
            order_state=order_state,
            execution_status=(execution_result or {}).get("status"),
        )
    except Exception:
        logger.debug("post_execution scheduling skipped", exc_info=True)


def _record_decision_for_intent(
    intent: ExecutionIntent,
    *,
    execution_result: dict[str, Any] | None = None,
    realized_pnl_inr: float | None = None,
) -> None:
    if not intent.agent_id:
        return
    decision_map = {
        IntentAction.ENTER: "ENTER",
        IntentAction.ADJUST: "REVISE",
        IntentAction.EXIT: "EXIT",
        IntentAction.HOLD: "HOLD",
    }
    decision = decision_map.get(intent.action)
    if not decision:
        return
    try:
        from trade_integrations.autonomous_agents.mcp_actions import mcp_record_decision

        actions = []
        if execution_result:
            actions.append(f"bridge_execute:{execution_result.get('status')}")
            if execution_result.get("mode"):
                actions.append(str(execution_result["mode"]))
        mcp_record_decision(
            agent_id=intent.agent_id,
            decision=decision,
            rationale=intent.rationale or f"bridge intent {intent.action.value}",
            ticker=intent.underlying,
            actions_taken=actions or None,
            pnl_inr=realized_pnl_inr if decision == "EXIT" else None,
            strategy=intent.strategy,
            append_outcome=decision != "EXIT",
        )
    except Exception:
        logger.debug("decision record after intent skipped", exc_info=True)
