"""Equity delivery/MIS charge estimates (Zerodha-style)."""

from __future__ import annotations

from typing import Any


def _qty(leg: dict[str, Any]) -> int:
    if leg.get("quantity"):
        return max(1, int(leg["quantity"]))
    return max(1, int(leg.get("lots", 1)) * int(leg.get("lot_size", 1)))


def calculate_equity_charges(
    legs: list[dict[str, Any]],
    *,
    product: str = "CNC",
    broker_preset: str = "zerodha",
) -> dict[str, Any]:
    """Per-leg equity charges for stock trades."""
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
        qty = _qty(leg)
        turnover = price * qty
        side = str(leg.get("side", "BUY")).upper()
        brokerage = 0.0 if product == "CNC" else min(20.0, turnover * 0.0003)
        if product == "CNC":
            brokerage = min(20.0, turnover * 0.0003) if side == "BUY" else min(20.0, turnover * 0.0003)
        stt = turnover * 0.001 if side == "SELL" else 0.0
        exchange = turnover * 0.0000345
        gst = (brokerage + exchange) * 0.18
        stamp = turnover * 0.00015 if side == "BUY" else 0.0
        sebi = turnover * 0.000001
        total = brokerage + stt + exchange + gst + stamp + sebi
        row = {
            "symbol": leg.get("symbol"),
            "side": side,
            "product": product,
            "brokerage": round(brokerage, 2),
            "stt": round(stt, 2),
            "exchange": round(exchange, 2),
            "gst": round(gst, 2),
            "stamp": round(stamp, 2),
            "sebi": round(sebi, 2),
            "total_charges": round(total, 2),
            "turnover": round(turnover, 2),
            "source": broker_preset,
        }
        per_leg.append(row)
        for k in totals:
            if k in row:
                totals[k] += row[k]
    totals["total_charges"] = round(
        totals["brokerage"] + totals["stt"] + totals["exchange"]
        + totals["gst"] + totals["stamp"] + totals["sebi"],
        2,
    )
    for k in totals:
        totals[k] = round(totals[k], 2)
    net = sum(
        -float(l.get("price", 0)) * _qty(l) if l.get("side") == "BUY" else float(l.get("price", 0)) * _qty(l)
        for l in legs
    )
    return {
        "per_leg": per_leg,
        "total": totals,
        "broker_preset": broker_preset,
        "net_debit_credit": round(net, 2),
        "charge_source": broker_preset,
    }


def build_stock_payoff(
    entry: float,
    quantity: int,
    *,
    target: float | None = None,
    stop: float | None = None,
) -> dict[str, Any]:
    """Simple stock P&L samples at entry, target, stop."""
    samples = [{"price": entry, "pnl": 0.0}]
    if target:
        samples.append({"price": target, "pnl": round((target - entry) * quantity, 2)})
    if stop:
        samples.append({"price": stop, "pnl": round((stop - entry) * quantity, 2)})
    return {"entry": entry, "quantity": quantity, "samples": samples}
