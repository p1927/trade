"""Read-only strategy progress snapshot for revision turns."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from trade_integrations.autonomous_agents.mandate import mandate_config_from_agent
from trade_integrations.execution.profile import resolve_profile
from trade_integrations.execution.routing_context import resolve_agent_routing


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _minutes_between(start: datetime | None, end: datetime | None) -> int | None:
    if not start or not end:
        return None
    return max(0, int((end - start).total_seconds() / 60))


def _hub_research_meta(*, focus: str, routing: Any) -> dict[str, Any]:
    """Load hub research timestamps and recommended strategy (read-only)."""
    meta: dict[str, Any] = {
        "focus": focus,
        "asset_type": routing.research_asset_type,
        "as_of": None,
        "recommended_strategy": None,
        "prediction_direction": None,
    }
    doc = None
    try:
        asset = routing.research_asset_type
        if asset == "index":
            from trade_integrations.context.hub import load_index_research_json

            doc = load_index_research_json(focus)
        elif asset == "stock":
            from trade_integrations.context.hub import load_stock_research_json

            doc = load_stock_research_json(focus)
        else:
            from trade_integrations.context.hub import load_options_research_json

            doc = load_options_research_json(focus)
    except Exception:
        return meta

    if not doc:
        return meta

    as_of = getattr(doc, "as_of", None)
    if isinstance(as_of, datetime):
        meta["as_of"] = as_of.isoformat()
    elif as_of:
        meta["as_of"] = str(as_of)

    recommended = dict(getattr(doc, "recommended", None) or {})
    ranked = list(getattr(doc, "ranked_strategies", None) or [])
    if recommended.get("name"):
        meta["recommended_strategy"] = recommended.get("name")
    elif ranked and isinstance(ranked[0], dict):
        meta["recommended_strategy"] = ranked[0].get("name")

    prediction = dict(getattr(doc, "prediction", None) or {})
    if prediction.get("direction"):
        meta["prediction_direction"] = prediction.get("direction")

    return meta


def _resolve_widget_id(
    *,
    agent: dict[str, Any],
    handoff: Any | None,
    lifecycle: dict[str, Any] | None,
) -> str | None:
    for source in (
        getattr(handoff, "widget_id", None) if handoff else None,
        (lifecycle or {}).get("active_widget_id"),
        (agent.get("last_decision") or {}).get("widget_id"),
    ):
        wid = str(source or "").strip()
        if wid:
            return wid
    return None


def _plan_summary(*, widget_id: str | None) -> dict[str, Any]:
    out: dict[str, Any] = {"widget_id": widget_id, "strategy": None, "leg_count": 0}
    if not widget_id:
        return out
    try:
        from trade_integrations.trade_widgets.store import load_trade_widget

        widget = load_trade_widget(widget_id)
    except Exception:
        return out
    if not widget:
        return out
    recommended = dict(widget.get("recommended") or {})
    legs = recommended.get("legs") or recommended.get("implementation_legs") or []
    out["strategy"] = recommended.get("name") or recommended.get("strategy")
    out["leg_count"] = len(legs) if isinstance(legs, list) else 0
    out["underlying"] = widget.get("underlying") or widget.get("ticker")
    return out


def _bridge_position_block(*, agent_id: str, focus: str, handoff: Any | None = None) -> dict[str, Any]:
    from trade_integrations.execution.bridge_intent import build_bridge_market_feedback
    from nautilus_openalgo_bridge.reconcile import total_unrealized_pnl

    feedback = build_bridge_market_feedback(agent_id=agent_id, ticker=focus)
    if feedback.get("status") != "ok":
        return {"source": "bridge", "error": feedback.get("error")}

    scoped_positions: list[dict[str, Any]] = []
    try:
        from nautilus_openalgo_bridge.agent_scoping import filter_positions_for_agent
        from nautilus_openalgo_bridge.openalgo_client import get_openalgo_client

        scoped_positions = filter_positions_for_agent(get_openalgo_client().get_position_book(), agent_id)
    except Exception:
        scoped_positions = []

    if handoff is None:
        try:
            from nautilus_openalgo_bridge.handoff import load_handoff

            handoff = load_handoff(agent_id)
        except Exception:
            handoff = None

    quotes = dict(feedback.get("quotes") or {})
    spot_row = quotes.get(str(focus).upper()) or {}
    spot = spot_row.get("ltp") or spot_row.get("last_price") or spot_row.get("close")
    handoff_leg_count = len(handoff.legs) if handoff and handoff.legs else 0
    open_positions = len(scoped_positions)

    block: dict[str, Any] = {
        "source": "bridge",
        "watch_configured": handoff is not None,
        "open_legs": handoff_leg_count,
        "open_positions": open_positions,
        "position_tracked": open_positions > 0,
        "spot": float(spot) if spot is not None else None,
        "unrealized_pnl_inr": total_unrealized_pnl(scoped_positions),
        "alerts": list(feedback.get("alerts") or [])[:3],
    }
    if handoff:
        block["entry_spot"] = handoff.entry_spot
        block["handoff_created_at"] = handoff.created_at
        block["handoff_widget_id"] = handoff.widget_id
        if handoff.entry_spot and block.get("spot"):
            block["spot_move_pct"] = round(
                (float(block["spot"]) - float(handoff.entry_spot))
                / float(handoff.entry_spot)
                * 100,
                2,
            )
    if handoff_leg_count > 0 and open_positions == 0:
        block["handoff_book_mismatch"] = True
    order_state = _order_state_block(agent_id=agent_id, focus=focus)
    if order_state:
        block["order_state"] = order_state
    return block


def _order_state_block(*, agent_id: str, focus: str) -> dict[str, Any] | None:
    try:
        from nautilus_openalgo_bridge.reconcile import read_order_state_for_agent

        state = read_order_state_for_agent(agent_id, underlying=focus)
        if state.get("error"):
            return {"error": state.get("error")}
        if not state.get("orders") and not any(state.get(k) for k in ("filled", "pending", "rejected")):
            return None
        return state
    except Exception:
        return None


def _lifecycle_block(*, agent_id: str, profile: Any) -> dict[str, Any]:
    block: dict[str, Any] = {
        "state": None,
        "active_strategy": None,
        "entered_at": None,
        "active_widget_id": None,
    }
    try:
        from trade_integrations.autonomous_agents.lifecycle import load_agent_lifecycle
        from trade_integrations.autonomous_agents.store import get_agent

        agent = get_agent(agent_id) or {}
        lifecycle = load_agent_lifecycle(agent)
        block.update(
            {
                "state": lifecycle.get("state"),
                "active_strategy": lifecycle.get("active_strategy"),
                "entered_at": lifecycle.get("entered_at"),
                "active_widget_id": lifecycle.get("active_widget_id"),
            }
        )
    except Exception:
        pass
    return block


def _timing_block(
    *,
    agent: dict[str, Any],
    mandate: Any,
    lifecycle: dict[str, Any],
    handoff_meta: dict[str, Any],
) -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    entered_at = _parse_iso(lifecycle.get("entered_at"))
    plan_approved_at = _parse_iso(str(agent.get("plan_approved_at") or ""))
    thesis_updated_at = _parse_iso(str((agent.get("thesis") or {}).get("updated_at") or ""))
    last_decision_at = _parse_iso(str((agent.get("last_decision") or {}).get("at") or ""))
    handoff_created_at = _parse_iso(str(handoff_meta.get("handoff_created_at") or ""))

    block: dict[str, Any] = {
        "holding_period": mandate.holding_period,
        "flatten_policy": mandate.flatten_policy,
        "minutes_since_entry": _minutes_between(entered_at, now),
        "minutes_since_plan_approved": _minutes_between(plan_approved_at, now),
        "minutes_since_thesis_update": _minutes_between(thesis_updated_at, now),
        "minutes_since_last_decision": _minutes_between(last_decision_at, now),
        "minutes_since_handoff": _minutes_between(handoff_created_at, now),
    }
    if mandate.holding_period == "intraday":
        try:
            from trade_integrations.autonomous_agents.market_hours import minutes_to_session_close
            from trade_integrations.autonomous_agents.trading_config import get_agent_trading_config

            block["minutes_to_session_close"] = minutes_to_session_close(get_agent_trading_config())
        except Exception:
            pass
    return block


def _progress_assessment(
    *,
    thesis: dict[str, Any],
    hub: dict[str, Any],
    position: dict[str, Any],
    plan: dict[str, Any],
    lifecycle: dict[str, Any],
) -> dict[str, Any]:
    open_count = int(position.get("open_positions") or 0)
    position_state = "open" if open_count > 0 else "flat"

    thesis_strategy = thesis.get("strategy")
    plan_strategy = plan.get("strategy")
    active_strategy = lifecycle.get("active_strategy") or plan_strategy
    strategy_aligned = True
    if position_state == "open" and thesis_strategy and active_strategy:
        strategy_aligned = str(thesis_strategy).lower() == str(active_strategy).lower()

    hub_as_of = _parse_iso(str(hub.get("as_of") or ""))
    thesis_updated = _parse_iso(str(thesis.get("updated_at") or ""))
    hub_stale_vs_thesis = False
    if hub_as_of and thesis_updated and hub_as_of < thesis_updated:
        hub_stale_vs_thesis = True

    notes: list[str] = []
    if position_state == "flat":
        notes.append("flat — no agent-scoped positionbook rows")
    if int(position.get("open_legs") or 0) > 0 and open_count == 0:
        notes.append("handoff legs exist but positionbook is flat — reconcile before REVISE")
    if not strategy_aligned:
        notes.append("thesis strategy differs from active lifecycle/handoff strategy")
    if hub_stale_vs_thesis:
        notes.append("hub research older than thesis — consider refresh before REVISE")
    if position.get("alerts"):
        notes.append("bridge alerts present — address before HOLD")

    return {
        "position_state": position_state,
        "strategy_aligned": strategy_aligned,
        "hub_stale_vs_thesis": hub_stale_vs_thesis,
        "notes": notes,
    }


def read_strategy_progress_snapshot(*, agent: dict[str, Any]) -> dict[str, Any]:
    """Read-only progress context for revision prompts — no persistence."""
    agent_id = str(agent.get("id") or "").strip()
    if not agent_id:
        return {"snapshot": {}, "prompt_text": ""}

    profile = resolve_profile(agent=agent)
    routing = resolve_agent_routing(agent)
    symbols = list(agent.get("symbols") or (["SPY"] if profile.is_us else ["NIFTY"]))
    focus = symbols[0]
    mandate = mandate_config_from_agent(agent)
    thesis = dict(agent.get("thesis") or {})

    lifecycle = _lifecycle_block(agent_id=agent_id, profile=profile)
    position: dict[str, Any] = {"source": "none", "open_positions": 0}
    handoff = None
    if profile.uses_nautilus_handoff:
        try:
            from nautilus_openalgo_bridge.handoff import load_handoff

            handoff = load_handoff(agent_id)
        except Exception:
            handoff = None
        position = _bridge_position_block(agent_id=agent_id, focus=focus, handoff=handoff)
    elif profile.is_us:
        position = {
            "source": "openalgo" if profile.backend == "openalgo" else "alpaca",
            "open_positions": 0,
        }
        try:
            from trade_integrations.execution.trading_port import adapter_for_agent

            rows = adapter_for_agent(agent).positionbook()
            matched = [r for r in rows if str(r.get("symbol") or "").upper() == str(focus).upper()]
            position["open_positions"] = len(matched)
            if matched:
                position["market_value"] = matched[0].get("market_value")
                position["unrealized_pl"] = matched[0].get("unrealized_pl")
        except Exception:
            pass

    hub = _hub_research_meta(focus=focus, routing=routing)
    widget_id = _resolve_widget_id(agent=agent, handoff=handoff, lifecycle=lifecycle)
    plan = _plan_summary(widget_id=widget_id)
    timing = _timing_block(
        agent=agent,
        mandate=mandate,
        lifecycle=lifecycle,
        handoff_meta=position,
    )
    assessment = _progress_assessment(
        thesis=thesis,
        hub=hub,
        position=position,
        plan=plan,
        lifecycle=lifecycle,
    )

    alert = dict(agent.get("last_bridge_alert") or {})
    snapshot: dict[str, Any] = {
        "agent_id": agent_id,
        "focus": focus,
        "connector_profile_id": agent.get("connector_profile_id"),
        "execution_market": profile.market,
        "mandate": {
            "holding_period": mandate.holding_period,
            "flatten_policy": mandate.flatten_policy,
            "revision_policy": mandate.revision_policy,
        },
        "thesis": {
            "strategy": thesis.get("strategy"),
            "direction": thesis.get("direction"),
            "confidence": thesis.get("confidence"),
            "decision": thesis.get("decision"),
            "updated_at": thesis.get("updated_at"),
        },
        "plan": plan,
        "hub_research": hub,
        "lifecycle": lifecycle,
        "position": position,
        "timing": timing,
        "assessment": assessment,
        "last_bridge_alert": {
            "signal": alert.get("signal"),
            "message": alert.get("message"),
            "at": alert.get("at"),
        }
        if alert
        else None,
    }
    return {"snapshot": snapshot, "prompt_text": ""}


def format_strategy_progress_for_prompt(*, agent: dict[str, Any], turn_kind: str) -> str:
    """Structured progress block for revision and post-execution turns."""
    if turn_kind not in {"strategy_revision", "post_execution"}:
        return ""
    try:
        payload = read_strategy_progress_snapshot(agent=agent)
    except Exception:
        return ""
    snapshot = payload.get("snapshot") or {}
    if not snapshot:
        return ""
    if turn_kind == "post_execution":
        header = (
            "## Post-execution state (mandatory — cite before HOLD/REVISE/EXIT)\n"
            "Confirm filled legs, order_state, unrealized P&L, and mandate time remaining.\n"
        )
        footer = (
            "- Update `watch_spec` if stop/target/entry levels changed.\n"
            "- If orders rejected or partial, REVISE or EXIT — do not assume full fill.\n"
        )
    else:
        header = (
            "## Strategy progress (mandatory — cite before REVISE/HOLD/EXIT)\n"
            "Compare live position vs approved plan, P&L vs mandate duration, and hub freshness.\n"
        )
        footer = (
            "- State whether the trade is **on track**, **needs revision**, or **should exit** vs the plan.\n"
            "- If spot/levels changed materially, update `watch_spec` via `set_agent_watch_spec`.\n"
        )
    return (
        f"{header}"
        f"```json\n{json.dumps(snapshot, indent=2, default=str)}\n```\n"
        f"{footer}"
    )
