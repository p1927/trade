"""Agent-scoped positionbook filtering for multi-agent paper on one OpenAlgo login."""

from __future__ import annotations

from typing import Any

from nautilus_openalgo_bridge.handoff import load_handoff
from nautilus_openalgo_bridge.hub_paths import load_agent_json
from nautilus_openalgo_bridge.instruments import normalize_watch_symbol, position_rows_to_legs
from nautilus_openalgo_bridge.models import ExecutionLeg
from nautilus_openalgo_bridge.reconcile import open_positions_from_book


def strategy_tag_for_agent(agent_id: str) -> str:
    agent = load_agent_json(agent_id) or {}
    constraints = dict(agent.get("constraints") or {})
    tag = str(
        constraints.get("bridge_strategy")
        or constraints.get("openalgo_strategy")
        or agent.get("openalgo_strategy")
        or ""
    ).strip()
    if tag:
        return tag
    aid = agent_id.strip()
    if aid.startswith("aa_"):
        return aid
    return f"aa_{aid}"


def _row_strategy(row: dict[str, Any]) -> str:
    return str(row.get("strategy") or row.get("tag") or "").strip()


def agent_symbol_universe(agent_id: str) -> set[str]:
    agent = load_agent_json(agent_id) or {}
    symbols = {normalize_watch_symbol(str(s)) for s in (agent.get("symbols") or []) if str(s).strip()}
    handoff = load_handoff(agent_id)
    if handoff:
        symbols.add(normalize_watch_symbol(handoff.underlying))
        for leg in handoff.legs or []:
            symbols.add(normalize_watch_symbol(leg.symbol))
    return {s for s in symbols if s}


def filter_positions_for_agent(rows: list[dict[str, Any]], agent_id: str) -> list[dict[str, Any]]:
    """Filter OpenAlgo position rows to one autonomous agent."""
    open_rows = open_positions_from_book(rows)
    if not agent_id:
        return open_rows

    tag = strategy_tag_for_agent(agent_id)
    tagged = [row for row in open_rows if _row_strategy(row) == tag]
    if tagged:
        return tagged

    universe = agent_symbol_universe(agent_id)
    if not universe:
        return []

    scoped: list[dict[str, Any]] = []
    handoff = load_handoff(agent_id)
    underlying = normalize_watch_symbol(handoff.underlying) if handoff else ""
    for row in open_rows:
        symbol = normalize_watch_symbol(str(row.get("symbol") or row.get("tradingsymbol") or ""))
        row_ul = normalize_watch_symbol(str(row.get("underlying") or row.get("underlyingsymbol") or ""))
        if symbol in universe or row_ul in universe:
            scoped.append(row)
            continue
        if any(symbol.startswith(ul) or (row_ul and (row_ul == ul or row_ul.startswith(ul))) for ul in universe):
            scoped.append(row)
            continue
        if underlying and (symbol.startswith(underlying) or row_ul == underlying):
            scoped.append(row)
    return scoped


def closing_legs_from_positions(rows: list[dict[str, Any]], *, underlying: str) -> list[ExecutionLeg]:
    """Build market close legs (opposite side) from open position rows."""
    legs: list[ExecutionLeg] = []
    for leg in position_rows_to_legs(rows, underlying=underlying):
        close_action = "SELL" if leg.action == "BUY" else "BUY"
        legs.append(
            ExecutionLeg(
                symbol=leg.symbol,
                exchange=leg.exchange,
                action=close_action,
                quantity=leg.quantity,
                product=leg.product,
                order_type=leg.order_type,
            )
        )
    return legs


def resolve_exit_legs_for_agent(
    *,
    agent_id: str,
    position_rows: list[dict[str, Any]],
    underlying: str,
    explicit_legs: list[ExecutionLeg] | None = None,
) -> list[ExecutionLeg]:
    if explicit_legs:
        return list(explicit_legs)
    scoped = filter_positions_for_agent(position_rows, agent_id)
    ul = str(underlying or "").strip().upper()
    if not ul:
        handoff = load_handoff(agent_id)
        if handoff:
            ul = handoff.underlying.upper()
        else:
            agent = load_agent_json(agent_id) or {}
            syms = agent.get("symbols") or ["NIFTY"]
            ul = str(syms[0]).upper()
    return closing_legs_from_positions(scoped, underlying=ul)
