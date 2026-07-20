"""Equity delivery/MIS charge estimates via broker presets."""

from __future__ import annotations

from typing import Any


def calculate_equity_charges(
    legs: list[dict[str, Any]],
    *,
    product: str = "CNC",
    broker_preset: str | None = None,
    include_exit: bool = True,
) -> dict[str, Any]:
    """Per-leg equity charges using shared broker_charges presets."""
    from trade_integrations.dataflows.broker_charges.calculate import (
        calculate_equity_charges_for_legs,
    )
    from trade_integrations.research.broker_context import resolve_broker_preset

    broker = broker_preset or resolve_broker_preset()
    return calculate_equity_charges_for_legs(
        legs,
        broker=broker,
        product=product,
        include_exit=include_exit,
    )


def build_stock_payoff(
    entry: float,
    quantity: int,
    *,
    target: float | None = None,
    stop: float | None = None,
    entry_charges: float = 0.0,
    exit_charges: float = 0.0,
) -> dict[str, Any]:
    """Stock P&L at entry, target, stop with gross and net max P/L."""
    samples = [{"price": entry, "pnl": 0.0, "spot": entry}]
    gross_max_profit = None
    gross_max_loss = None

    if target:
        gp = round((target - entry) * quantity, 2)
        samples.append({"price": target, "pnl": gp, "spot": target})
        gross_max_profit = gp
    if stop:
        gl = round((stop - entry) * quantity, 2)
        samples.append({"price": stop, "pnl": gl, "spot": stop})
        gross_max_loss = gl

    net_max_profit = (
        round(gross_max_profit - entry_charges - exit_charges, 2)
        if gross_max_profit is not None
        else None
    )
    net_max_loss = (
        round(gross_max_loss - entry_charges - exit_charges, 2)
        if gross_max_loss is not None
        else None
    )

    return {
        "entry": entry,
        "quantity": quantity,
        "samples": samples,
        "max_profit": gross_max_profit,
        "max_loss": gross_max_loss,
        "gross_max_profit": gross_max_profit,
        "gross_max_loss": gross_max_loss,
        "net_max_profit": net_max_profit,
        "net_max_loss": net_max_loss,
    }


def compute_stock_payoff_over_time(
    entry: float,
    quantity: int,
    *,
    horizon_days: int = 14,
    target: float | None = None,
    entry_charges: float = 0.0,
    exit_charges: float = 0.0,
) -> dict[str, Any]:
    """Hold-horizon P&L samples (linear path to target; theta=0)."""
    days = max(1, int(horizon_days))
    samples: list[dict[str, Any]] = []
    for day in range(days + 1):
        if target is not None:
            price = entry + (target - entry) * (day / days)
        else:
            price = entry
        pnl = round((price - entry) * quantity, 2)
        exit_cost = exit_charges if day == days else 0.0
        net_pnl = round(pnl - entry_charges - exit_cost, 2)
        samples.append(
            {
                "day": day,
                "days_to_expiry": days - day,
                "price": round(price, 2),
                "pnl": pnl,
                "net_pnl": net_pnl,
            }
        )
    gross_max = max((s["pnl"] for s in samples), default=0.0)
    gross_min = min((s["pnl"] for s in samples), default=0.0)
    net_max = max((s["net_pnl"] for s in samples), default=0.0)
    net_min = min((s["net_pnl"] for s in samples), default=0.0)
    return {
        "samples": samples,
        "gross_max_profit": gross_max,
        "gross_max_loss": gross_min,
        "net_max_profit": net_max,
        "net_max_loss": net_min,
    }
