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
from nautilus_openalgo_bridge.models import ExecutionIntent, IntentAction, PositionHandoff
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
        except Exception:
            logger.debug("leg validation skipped", exc_info=True)

    handoff.legs = legs
    handoff.underlying = ul.upper()
    save_handoff(handoff)
    update_agent_thesis_from_handoff(handoff)
    return handoff


def reconcile_after_intent(
    intent: ExecutionIntent,
    *,
    client: BridgeOpenAlgoClient | None = None,
    execution_result: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Post-flight: positionbook → handoff + agent thesis + decision log."""
    agent_id = str(intent.agent_id or "").strip()
    if not agent_id:
        return {"status": "skipped", "reason": "no_agent_id"}

    oa = client or get_openalgo_client()
    rows = oa.get_position_book()
    if agent_id:
        from nautilus_openalgo_bridge.agent_scoping import filter_positions_for_agent

        open_rows = filter_positions_for_agent(rows, agent_id)
    else:
        open_rows = open_positions_from_book(rows)
    pnl = total_unrealized_pnl(open_rows)

    payload: dict[str, Any] = {
        "open_positions": len(open_rows),
        "unrealized_pnl_inr": pnl,
    }

    if intent.action == IntentAction.EXIT:
        payload["handoff"] = "cleared_on_exit"
        payload["open_positions"] = len(open_rows)
        payload["unrealized_pnl_inr"] = pnl
        _record_decision_for_intent(intent, execution_result=execution_result)
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

    _record_decision_for_intent(intent, execution_result=execution_result)
    return payload


def _record_decision_for_intent(
    intent: ExecutionIntent,
    *,
    execution_result: dict[str, Any] | None = None,
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
        )
    except Exception:
        logger.debug("decision record after intent skipped", exc_info=True)
