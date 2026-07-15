"""Payoff curve, PoP estimate, and India F&O charges."""

from __future__ import annotations

import math
from typing import Any


def _leg_pnl_at_price(leg: dict[str, Any], underlying: float) -> float:
    side_mult = 1 if leg.get("side") == "BUY" else -1
    qty = int(leg.get("quantity") or leg.get("lot_size", 1) * leg.get("lots", 1))
    premium = float(leg.get("price") or 0)
    strike = float(leg.get("strike") or 0)
    opt = leg.get("option_type", "CE")
    intrinsic = max(0.0, underlying - strike) if opt == "CE" else max(0.0, strike - underlying)
    return side_mult * qty * (intrinsic - premium)


def compute_payoff(
    legs: list[dict[str, Any]],
    spot: float,
    *,
    steps: int = 80,
    range_pct: float = 0.12,
) -> dict[str, Any]:
    """Expiry P&L curve and breakevens."""
    if spot <= 0 or not legs:
        return {"samples": [], "breakevens": [], "max_profit": None, "max_loss": None}

    lo = spot * (1 - range_pct)
    hi = spot * (1 + range_pct)
    step = (hi - lo) / steps
    samples = []
    max_profit = -math.inf
    max_loss = math.inf
    prev_pnl = None
    breakevens: list[float] = []

    for i in range(steps + 1):
        px = lo + i * step
        pnl = sum(_leg_pnl_at_price(leg, px) for leg in legs)
        samples.append({"underlying": round(px, 2), "pnl": round(pnl, 2)})
        max_profit = max(max_profit, pnl)
        max_loss = min(max_loss, pnl)
        if prev_pnl is not None and prev_pnl * pnl < 0:
            breakevens.append(round(px, 2))
        prev_pnl = pnl

    return {
        "samples": samples,
        "breakevens": breakevens,
        "max_profit": round(max_profit, 2) if max_profit != -math.inf else None,
        "max_loss": round(max_loss, 2) if max_loss != math.inf else None,
    }


def _estimate_pop(payoff: dict[str, Any], spot: float) -> float:
    samples = payoff.get("samples") or []
    if not samples:
        return 0.5
    profitable = sum(1 for s in samples if s.get("pnl", 0) > 0)
    return profitable / len(samples)


def calculate_charges(
    legs: list[dict[str, Any]],
    *,
    broker_preset: str = "zerodha",
) -> dict[str, Any]:
    """Per-leg and total charges for options round-trip."""
    per_leg: list[dict[str, Any]] = []
    totals = {
        "brokerage": 0.0,
        "stt": 0.0,
        "exchange": 0.0,
        "gst": 0.0,
        "stamp": 0.0,
        "sebi": 0.0,
        "total_charges": 0.0,
    }

    for leg in legs:
        price = float(leg.get("price") or 0)
        qty = int(leg.get("quantity") or leg.get("lot_size", 1) * leg.get("lots", 1))
        turnover = price * qty
        side = leg.get("side", "BUY")
        leg_charges = _zerodha_options_leg(turnover, side)
        per_leg.append({"symbol": leg.get("symbol"), "side": side, **leg_charges})
        for k in totals:
            if k in leg_charges:
                totals[k] += leg_charges[k]

    totals["total_charges"] = round(
        totals["brokerage"] + totals["stt"] + totals["exchange"] + totals["gst"]
        + totals["stamp"] + totals["sebi"],
        2,
    )
    for k in totals:
        totals[k] = round(totals[k], 2)
    return {"per_leg": per_leg, "total": totals, "broker_preset": broker_preset}


def _zerodha_options_leg(turnover: float, side: str) -> dict[str, float]:
    """Zerodha-style F&O charge estimate (flat brokerage per order)."""
    brokerage = 20.0
    stt = turnover * 0.000625 if side == "SELL" else 0.0
    exchange = turnover * 0.0003503
    sebi = turnover * 0.000001
    gst = 0.18 * (brokerage + exchange + sebi)
    stamp = turnover * 0.00003 if side == "BUY" else 0.0
    total = brokerage + stt + exchange + gst + stamp + sebi
    return {
        "brokerage": round(brokerage, 2),
        "stt": round(stt, 2),
        "exchange": round(exchange, 2),
        "gst": round(gst, 2),
        "stamp": round(stamp, 2),
        "sebi": round(sebi, 2),
        "total_charges": round(total, 2),
        "turnover": round(turnover, 2),
    }


def estimate_strategy_metrics(
    legs: list[dict[str, Any]],
    *,
    spot: float,
    broker_preset: str = "zerodha",
) -> dict[str, Any]:
    payoff = compute_payoff(legs, spot)
    charges = calculate_charges(legs, broker_preset=broker_preset)
    pop = _estimate_pop(payoff, spot)
    return {
        "payoff": payoff,
        "charges": charges,
        "pop": pop,
        "max_profit": payoff.get("max_profit"),
        "max_loss": payoff.get("max_loss"),
        "breakevens": payoff.get("breakevens"),
    }


def build_implementation_steps(
    recommended: dict[str, Any],
    *,
    options_exchange: str,
) -> list[dict[str, Any]]:
    """Numbered steps with MCP payloads for Vibe execution."""
    legs = recommended.get("legs") or []
    steps: list[dict[str, Any]] = [
        {
            "step": 1,
            "action": "preview",
            "description": "Review recommended legs, payoff, and charges in hub JSON / Strategy Builder",
            "mcp_tool": None,
            "payload": None,
        },
        {
            "step": 2,
            "action": "margin_check",
            "description": "Verify margin before placing orders",
            "mcp_tool": "calculate_margin",
            "payload": {
                "positions": [
                    {
                        "symbol": leg.get("symbol"),
                        "exchange": options_exchange,
                        "action": leg.get("side"),
                        "quantity": str(leg.get("quantity")),
                        "product": "NRML",
                        "pricetype": "MARKET",
                        "price": "0",
                    }
                    for leg in legs
                    if leg.get("symbol")
                ]
            },
        },
        {
            "step": 3,
            "action": "confirm",
            "description": "User explicitly confirms live execution in chat",
            "mcp_tool": None,
            "payload": None,
        },
        {
            "step": 4,
            "action": "execute_basket",
            "description": "Place all legs via basket order (BUY legs first)",
            "mcp_tool": "place_basket_order",
            "payload": {
                "orders": [
                    {
                        "symbol": leg.get("symbol"),
                        "exchange": options_exchange,
                        "action": leg.get("side"),
                        "quantity": str(leg.get("quantity")),
                        "pricetype": "MARKET",
                        "product": "NRML",
                    }
                    for leg in legs
                    if leg.get("symbol")
                ]
            },
        },
    ]
    return steps
