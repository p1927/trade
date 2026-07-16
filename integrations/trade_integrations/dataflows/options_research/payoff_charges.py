"""Payoff curve, OptionLab PoP, finworth charges, and net P&L."""

from __future__ import annotations

import logging
import math
from datetime import date, datetime, timedelta
from typing import Any

logger = logging.getLogger(__name__)


def _leg_qty(leg: dict[str, Any]) -> int:
    return int(leg.get("quantity") or leg.get("lot_size", 1) * leg.get("lots", 1))


def _leg_pnl_at_price(leg: dict[str, Any], underlying: float) -> float:
    side_mult = 1 if leg.get("side") == "BUY" else -1
    qty = _leg_qty(leg)
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
        return {
            "samples": [],
            "breakevens": [],
            "max_profit": None,
            "max_loss": None,
            "gross_max_profit": None,
            "gross_max_loss": None,
        }

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

    gross_max = round(max_profit, 2) if max_profit != -math.inf else None
    gross_min = round(max_loss, 2) if max_loss != math.inf else None
    return {
        "samples": samples,
        "breakevens": breakevens,
        "max_profit": gross_max,
        "max_loss": gross_min,
        "gross_max_profit": gross_max,
        "gross_max_loss": gross_min,
    }


def compute_net_debit_credit(legs: list[dict[str, Any]]) -> float:
    """Net premium flow: positive = net credit received, negative = net debit paid."""
    total = 0.0
    for leg in legs:
        qty = _leg_qty(leg)
        premium = float(leg.get("price") or 0)
        flow = premium * qty
        if leg.get("side") == "SELL":
            total += flow
        else:
            total -= flow
    return round(total, 2)


def _parse_expiry_date(expiry: str | None) -> date | None:
    if not expiry:
        return None
    raw = expiry.strip().upper().replace("-", "")
    try:
        if len(raw) == 7:
            return datetime.strptime(raw, "%d%b%y").date()
        if len(raw) == 9:
            return datetime.strptime(raw, "%d%b%Y").date()
    except ValueError:
        pass
    for fmt in ("%d-%b-%Y", "%d-%m-%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(expiry.strip(), fmt).date()
        except ValueError:
            continue
    return None


def _optionlab_pop(
    legs: list[dict[str, Any]],
    *,
    spot: float,
    expiry: str | None = None,
    iv: float | None = None,
) -> float | None:
    try:
        from optionlab import run_strategy
    except ImportError:
        return None

    target = _parse_expiry_date(expiry) or (date.today() + timedelta(days=7))
    if target <= date.today():
        target = date.today() + timedelta(days=1)
    vol = (iv or 18.0) / 100.0 if (iv or 0) > 1 else (iv or 0.18)
    strategy = []
    for leg in legs:
        opt = leg.get("option_type", "CE")
        strategy.append(
            {
                "type": "call" if opt == "CE" else "put",
                "strike": float(leg.get("strike") or spot),
                "premium": float(leg.get("price") or 0),
                "n": _leg_qty(leg),
                "action": "buy" if leg.get("side") == "BUY" else "sell",
            }
        )
    if not strategy:
        return None
    try:
        out = run_strategy(
            {
                "stock_price": spot,
                "start_date": date.today(),
                "target_date": target,
                "volatility": vol,
                "interest_rate": 0.07,
                "min_stock": spot * 0.85,
                "max_stock": spot * 1.15,
                "strategy": strategy,
            }
        )
        pop = float(out.probability_of_profit)
        return round(pop, 4) if pop <= 1 else round(pop / 100, 4)
    except Exception as exc:
        logger.debug("OptionLab PoP failed: %s", exc)
        return None


def _sample_pop(payoff: dict[str, Any]) -> float:
    samples = payoff.get("samples") or []
    if not samples:
        return 0.5
    profitable = sum(1 for s in samples if s.get("pnl", 0) > 0)
    return profitable / len(samples)


def _finworth_leg_charges(turnover: float, side: str) -> dict[str, float]:
    """Per-leg F&O charges using finworth tax helpers + Zerodha flat brokerage."""
    try:
        import finworth as fw
    except ImportError:
        return _fallback_options_leg(turnover, side)

    brokerage = 20.0
    exchange = turnover * 0.0003503
    sebi = turnover * 0.000001
    stt = float(fw.stt(turnover, "options")) if side == "SELL" else 0.0
    stamp = float(fw.stamp_duty(turnover, "options")) if side == "BUY" else 0.0
    gst = float(fw.gst_on_brokerage(brokerage, exchange + sebi))
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
        "source": "finworth",
    }


def _fallback_options_leg(turnover: float, side: str) -> dict[str, float]:
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
        "source": "fallback",
    }


def calculate_charges(
    legs: list[dict[str, Any]],
    *,
    broker_preset: str = "zerodha",
) -> dict[str, Any]:
    """Per-leg and total charges for options entry."""
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
        qty = _leg_qty(leg)
        turnover = price * qty
        side = leg.get("side", "BUY")
        leg_charges = _finworth_leg_charges(turnover, side)
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

    net_debit_credit = compute_net_debit_credit(legs)
    return {
        "per_leg": per_leg,
        "total": totals,
        "broker_preset": broker_preset,
        "net_debit_credit": net_debit_credit,
        "charge_source": per_leg[0].get("source", "fallback") if per_leg else "fallback",
    }


def attach_net_pnl(
    payoff: dict[str, Any],
    charges: dict[str, Any],
) -> dict[str, Any]:
    """Add net max profit/loss after entry charges."""
    entry = float((charges.get("total") or {}).get("total_charges") or 0)
    gross_max = payoff.get("gross_max_profit")
    gross_min = payoff.get("gross_max_loss")
    net_max_profit = None
    net_max_loss = None
    if gross_max is not None:
        net_max_profit = round(gross_max - entry, 2)
    if gross_min is not None:
        net_max_loss = round(gross_min - entry, 2)
    payoff["net_max_profit"] = net_max_profit
    payoff["net_max_loss"] = net_max_loss
    payoff["entry_charges"] = entry
    return payoff


def estimate_strategy_metrics(
    legs: list[dict[str, Any]],
    *,
    spot: float,
    broker_preset: str = "zerodha",
    expiry: str | None = None,
    iv: float | None = None,
) -> dict[str, Any]:
    payoff = compute_payoff(legs, spot)
    charges = calculate_charges(legs, broker_preset=broker_preset)
    attach_net_pnl(payoff, charges)

    pop = _optionlab_pop(legs, spot=spot, expiry=expiry, iv=iv)
    pop_source = "optionlab" if pop is not None else "sample_ratio"
    if pop is None:
        pop = _sample_pop(payoff)

    return {
        "payoff": payoff,
        "charges": charges,
        "pop": pop,
        "pop_source": pop_source,
        "max_profit": payoff.get("gross_max_profit"),
        "max_loss": payoff.get("gross_max_loss"),
        "net_max_profit": payoff.get("net_max_profit"),
        "net_max_loss": payoff.get("net_max_loss"),
        "breakevens": payoff.get("breakevens"),
        "net_debit_credit": charges.get("net_debit_credit"),
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
            "description": "Review legs, gross/net payoff, and charges in hub JSON or Strategy Builder",
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
