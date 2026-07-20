"""Pre-flight checks before OpenAlgo execution."""

from __future__ import annotations

from typing import Any

from nautilus_openalgo_bridge.config import (
    BridgeConfig,
    get_bridge_config,
    is_bridge_exit_window_open,
    is_bridge_market_open,
)
from nautilus_openalgo_bridge.orders import legs_to_openalgo_orders
from nautilus_openalgo_bridge.models import ExecutionIntent, IntentAction
from nautilus_openalgo_bridge.openalgo_client import BridgeOpenAlgoClient


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


def run_preflight(
    intent: ExecutionIntent,
    client: BridgeOpenAlgoClient,
    config: BridgeConfig | None = None,
) -> dict[str, Any]:
    """Validate market hours, analyzer mode, and margin before execution."""
    cfg = config or get_bridge_config()
    action = intent.action
    checks: dict[str, Any] = {"action": action.value}

    if cfg.paper_only:
        try:
            if not client.ensure_analyzer_mode():
                return {"blocked": True, "reason": "analyzer_mode", "checks": checks}
        except RuntimeError as exc:
            return {"blocked": True, "reason": "analyzer_mode", "error": str(exc), "checks": checks}
        checks["analyzer_mode"] = True

    if action == IntentAction.EXIT:
        import os

        autonomous = bool(str(intent.agent_id or "").strip())
        analyzer_bypass = os.getenv("ANALYZER", "").strip() == "1"
        if autonomous or not analyzer_bypass:
            if not is_bridge_exit_window_open(cfg):
                return {"blocked": True, "reason": "outside_exit_window", "checks": checks}
            checks["exit_window_open"] = True
        else:
            checks["paper_exit_analyzer_bypass"] = True

        if cfg.paper_only:
            try:
                if not client.ensure_analyzer_mode():
                    return {"blocked": True, "reason": "analyzer_mode", "checks": checks}
            except RuntimeError as exc:
                return {"blocked": True, "reason": "analyzer_mode", "error": str(exc), "checks": checks}
            checks["analyzer_mode"] = True
        return {"blocked": False, "checks": checks}

    if action in (IntentAction.ENTER, IntentAction.ADJUST):
        if not is_bridge_market_open(cfg):
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
            checks["margin_warning"] = "margin_unavailable"
        return {"blocked": False, "checks": checks}

    return {"blocked": False, "checks": checks}
