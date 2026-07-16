"""OpenAlgo order mapping for bridge execution intents."""

from __future__ import annotations

from typing import Any

from nautilus_openalgo_bridge.models import ExecutionLeg


def leg_to_openalgo_order(leg: ExecutionLeg) -> dict[str, Any]:
    order: dict[str, Any] = {
        "symbol": leg.symbol,
        "exchange": leg.exchange,
        "action": leg.action,
        "quantity": leg.quantity,
        "product": leg.product,
        "pricetype": leg.order_type,
    }
    if leg.price is not None:
        order["price"] = leg.price
    return order


def legs_to_openalgo_orders(legs: list[ExecutionLeg]) -> list[dict[str, Any]]:
    orders: list[dict[str, Any]] = []
    for leg in legs:
        if not leg.symbol or leg.quantity <= 0:
            continue
        orders.append(leg_to_openalgo_order(leg))
    return orders
