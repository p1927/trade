"""India F&O charge wrappers for execution simulation."""

from __future__ import annotations

from typing import Any

from trade_integrations.dataflows.broker_charges.calculate import calculate_leg_charges


def _sum_leg_charges(legs: list[dict[str, Any]], *, broker: str | None = None) -> dict[str, Any]:
    breakdown = [calculate_leg_charges(leg, broker=broker) for leg in legs]
    total = sum(float(row.get("total_charges") or row.get("total") or 0.0) for row in breakdown)
    return {"legs": breakdown, "total_charges_inr": round(total, 2)}


def nifty_futures_round_trip_charges(
    *,
    price: float,
    lots: int = 1,
    lot_size: int = 25,
    broker: str | None = None,
) -> dict[str, Any]:
    qty = lots * lot_size
    legs = [
        {"symbol": "NIFTY FUT", "side": "BUY", "price": price, "quantity": qty, "segment": "FUTURE"},
        {"symbol": "NIFTY FUT", "side": "SELL", "price": price, "quantity": qty, "segment": "FUTURE"},
    ]
    result = _sum_leg_charges(legs, broker=broker)
    return {
        "instrument": "nifty_futures",
        "lots": lots,
        "lot_size": lot_size,
        **result,
    }


def bull_call_spread_charges(
    *,
    long_strike_price: float,
    short_strike_price: float,
    lots: int = 1,
    lot_size: int = 25,
    broker: str | None = None,
) -> dict[str, Any]:
    qty = lots * lot_size
    legs = [
        {"symbol": "NIFTY CE", "side": "BUY", "price": long_strike_price, "quantity": qty, "segment": "OPTION"},
        {"symbol": "NIFTY CE", "side": "SELL", "price": short_strike_price, "quantity": qty, "segment": "OPTION"},
    ]
    result = _sum_leg_charges(legs, broker=broker)
    result["instrument"] = "bull_call_spread"
    return result
