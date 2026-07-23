"""Pre-flight checks before OpenAlgo execution."""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import Any

from nautilus_openalgo_bridge.config import (
    BridgeConfig,
    get_bridge_config,
    is_bridge_market_open,
)
from nautilus_openalgo_bridge.orders import legs_to_openalgo_orders
from nautilus_openalgo_bridge.models import ExecutionIntent, IntentAction
from nautilus_openalgo_bridge.openalgo_client import BridgeOpenAlgoClient

logger = logging.getLogger(__name__)

STALE_HANDOFF_CONTEXT_MINUTES = 15


def _parse_context_generation_timestamp(generation: str) -> datetime | None:
    raw = str(generation or "").strip()
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None


def _handoff_market_context_stale(
    handoff_generation: str,
    current_generation: str,
    *,
    max_age_minutes: float = STALE_HANDOFF_CONTEXT_MINUTES,
    handoff_file_mtime: float | None = None,
) -> bool:
    """True when stamped handoff context is too old or mismatched with a stale timestamp."""
    handoff_gen = str(handoff_generation or "").strip()
    current_gen = str(current_generation or "").strip()
    if not handoff_gen:
        return False

    ts = _parse_context_generation_timestamp(handoff_gen)
    age_minutes: float | None = None
    if ts is not None:
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        age_minutes = (datetime.now(timezone.utc) - ts.astimezone(timezone.utc)).total_seconds() / 60.0
        if age_minutes > max_age_minutes:
            return True

    if handoff_gen != current_gen:
        if ts is None:
            return True
        if age_minutes is not None and age_minutes > max_age_minutes:
            return True
        return False

    if ts is None and handoff_file_mtime is not None:
        age_minutes = (time.time() - handoff_file_mtime) / 60.0
        return age_minutes > max_age_minutes
    return False


def _margin_positions_from_orders(orders: list[dict[str, Any]]) -> list[dict[str, Any]]:
    positions: list[dict[str, Any]] = []
    for order in orders:
        positions.append(
            {
                "symbol": order.get("symbol"),
                "exchange": order.get("exchange"),
                "action": order.get("action"),
                "quantity": str(order.get("quantity")),
                "product": order.get("product", "NRML"),
                "pricetype": order.get("pricetype", "MARKET"),
                "price": "0",
            }
        )
    return positions


def _verify_execution_context(
    intent: ExecutionIntent,
    client: BridgeOpenAlgoClient,
    cfg: BridgeConfig,
    checks: dict[str, Any],
) -> dict[str, Any] | None:
    """Return blocked preflight payload when market context disagrees with mandate."""
    agent_id = str(intent.agent_id or "").strip()
    if not cfg.paper_only and not agent_id:
        return None

    from trade_integrations.execution.context_verify import (
        apply_context_verification,
        verify_agent_execution_context,
    )

    try:
        market_context = client.get_market_context()
    except Exception as exc:
        logger.warning("marketcontext fetch failed: %s", exc)
        if cfg.paper_only or agent_id:
            return {
                "blocked": True,
                "reason": "market_context_unavailable",
                "error": str(exc),
                "checks": checks,
            }
        return None

    checks["context_generation"] = market_context.context_generation
    checks["execution_venue"] = market_context.execution_venue
    checks["positions_authority"] = market_context.positions_authority

    if agent_id:
        from nautilus_openalgo_bridge.handoff import load_handoff

        handoff = load_handoff(agent_id)
        handoff_gen = handoff.context_generation if handoff else None
        if handoff_gen:
            checks["handoff_context_generation"] = handoff_gen
            from nautilus_openalgo_bridge.handoff import handoff_mtime

            if _handoff_market_context_stale(
                handoff_gen,
                market_context.context_generation,
                handoff_file_mtime=handoff_mtime(agent_id),
            ):
                return {
                    "blocked": True,
                    "reason": "stale_market_context",
                    "reload_hint": (
                        "Reload watch handoff context: "
                        "trade start nautilus-watch --registry or re-run ensure_nautilus_watch_for_agent"
                    ),
                    "checks": checks,
                }

    agent: dict[str, Any] = {"constraints": {"mode": "paper"}}
    if agent_id:
        from trade_integrations.autonomous_agents.store import get_agent

        try:
            loaded = get_agent(agent_id)
        except Exception as exc:
            logger.warning("agent load failed during context verify for %s: %s", agent_id, exc)
            loaded = None
        if loaded:
            agent = loaded

    verification = verify_agent_execution_context(
        agent=agent,
        market_context=market_context,
        env_paper_lock=cfg.paper_only,
        allow_analyzer_sync=cfg.paper_only,
    )
    sync_requested = verification.action_taken == "analyzer_enabled"
    verification = apply_context_verification(
        verification,
        sync_analyzer=client.ensure_analyzer_mode,
    )
    if sync_requested:
        if verification.ok:
            logger.info("preflight enabled analyzer under env lock: %s", verification.reason)
        else:
            logger.warning("preflight analyzer sync failed: %s", verification.reason)
    if verification.ok and sync_requested:
        try:
            market_context = client.get_market_context()
            checks["context_generation"] = market_context.context_generation
            checks["execution_venue"] = market_context.execution_venue
            checks["positions_authority"] = market_context.positions_authority
        except Exception as exc:
            logger.warning("marketcontext refetch after sync failed: %s", exc)
    checks["context_verification"] = verification.reason
    checks["analyzer_mode"] = bool(market_context.analyze_mode) if verification.ok else False

    if not verification.ok:
        reason = verification.reason
        if "paper_mandate" in reason or "analyzer" in reason:
            reason = "analyzer_mode"
        return {"blocked": True, "reason": reason, "checks": checks}
    return None


def run_preflight(
    intent: ExecutionIntent,
    client: BridgeOpenAlgoClient,
    config: BridgeConfig | None = None,
) -> dict[str, Any]:
    """Validate market hours, analyzer mode, and margin before execution."""
    cfg = config or get_bridge_config()
    action = intent.action
    checks: dict[str, Any] = {"action": action.value}

    blocked = _verify_execution_context(intent, client, cfg, checks)
    if blocked:
        return blocked

    if action == IntentAction.EXIT:
        import os

        from nautilus_openalgo_bridge.market_hours import is_exit_window_open_for_agent

        autonomous = bool(str(intent.agent_id or "").strip())
        analyzer_bypass = os.getenv("ANALYZER", "").strip() == "1"
        if autonomous or not analyzer_bypass:
            agent_id = str(intent.agent_id or "").strip() or None
            if not is_exit_window_open_for_agent(agent_id):
                return {"blocked": True, "reason": "outside_exit_window", "checks": checks}
            checks["exit_window_open"] = True
        else:
            checks["paper_exit_analyzer_bypass"] = True
        return {"blocked": False, "checks": checks}

    if action in (IntentAction.ENTER, IntentAction.ADJUST):
        agent_id = str(intent.agent_id or "").strip() or None
        if agent_id:
            from nautilus_openalgo_bridge.market_hours import is_agent_watch_session_open

            if not is_agent_watch_session_open(agent_id):
                return {"blocked": True, "reason": "outside_market_hours", "checks": checks}
        elif not is_bridge_market_open(cfg):
            return {"blocked": True, "reason": "outside_market_hours", "checks": checks}
        checks["market_open"] = True

        orders = legs_to_openalgo_orders(intent.legs)
        if not orders:
            return {"blocked": True, "reason": "no_valid_legs", "checks": checks}

        margin_positions = _margin_positions_from_orders(orders)
        try:
            margin = client.calculate_margin(margin_positions)
        except RuntimeError as exc:
            return {"blocked": True, "reason": "margin_check_failed", "error": str(exc), "checks": checks}

        checks["margin_inr"] = margin
        checks["orders"] = len(orders)
        if margin is None:
            return {"blocked": True, "reason": "margin_unavailable", "checks": checks}
        agent_id = str(intent.agent_id or "").strip()
        if agent_id:
            try:
                from trade_integrations.autonomous_agents.defaults import DEFAULT_BUDGET_INR
                from trade_integrations.autonomous_agents.store import get_agent

                agent = get_agent(agent_id)
                if agent is None:
                    return {"blocked": True, "reason": "agent_not_found", "checks": checks}
                constraints = dict(agent.get("constraints") or {})
                budget = float(constraints.get("budget_inr") or DEFAULT_BUDGET_INR)
                max_daily_loss = float(constraints.get("max_daily_loss_inr") or 2_000)
                if float(margin) > budget:
                    checks["budget_inr"] = budget
                    return {
                        "blocked": True,
                        "reason": "margin_exceeds_budget",
                        "checks": checks,
                    }
                charges = _estimate_charges_for_orders(orders, agent)
                if charges:
                    checks["charges"] = charges
                    round_trip = float(
                        charges.get("round_trip_charges")
                        or (charges.get("total") or {}).get("total_charges")
                        or 0
                    )
                    checks["round_trip_charges_inr"] = round_trip
                    if round_trip + float(margin) > budget:
                        return {
                            "blocked": True,
                            "reason": "margin_plus_charges_exceeds_budget",
                            "checks": checks,
                        }
                    if round_trip > max_daily_loss:
                        return {
                            "blocked": True,
                            "reason": "charges_exceed_max_daily_loss",
                            "checks": checks,
                        }
            except Exception as exc:
                logger.warning("budget check failed for agent %s: %s", agent_id, exc)
                return {
                    "blocked": True,
                    "reason": "budget_check_failed",
                    "error": str(exc),
                    "checks": checks,
                }
        return {"blocked": False, "checks": checks}

    return {"blocked": False, "checks": checks}


def _estimate_charges_for_orders(
    orders: list[dict[str, Any]],
    agent: dict[str, Any],
) -> dict[str, Any] | None:
    """Estimate round-trip charges for bridge basket orders."""
    if not orders:
        return None
    legs: list[dict[str, Any]] = []
    for order in orders:
        legs.append(
            {
                "symbol": order.get("symbol"),
                "side": order.get("action") or order.get("side"),
                "quantity": order.get("quantity"),
                "price": float(order.get("price") or 0),
                "product": order.get("product") or "NRML",
            }
        )
    broker = str((agent.get("constraints") or {}).get("broker_preset") or "indmoney")
    try:
        from trade_integrations.execution.routing_context import resolve_agent_routing

        routing = resolve_agent_routing(agent)
        if routing.primary_instrument == "equity":
            from trade_integrations.dataflows.broker_charges.calculate import (
                calculate_equity_charges_for_legs,
            )

            product = str(legs[0].get("product") or "NRML")
            return calculate_equity_charges_for_legs(legs, broker=broker, product=product)
        from trade_integrations.dataflows.broker_charges.calculate import calculate_charges_for_legs

        return calculate_charges_for_legs(legs, broker=broker)
    except Exception as exc:
        logger.debug("charge estimate skipped: %s", exc)
        return None
