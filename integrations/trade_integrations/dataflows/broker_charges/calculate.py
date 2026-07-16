"""Broker-specific F&O charge calculator using published preset constants."""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

_PRESETS_PATH = Path(__file__).with_name("presets.json")


@lru_cache(maxsize=1)
def load_presets() -> dict[str, Any]:
    return json.loads(_PRESETS_PATH.read_text(encoding="utf-8"))


def normalize_broker_id(broker: str | None) -> str:
    raw = (broker or "").strip().lower().replace(" ", "")
    aliases = {
        "indmoney": "indmoney",
        "ind": "indmoney",
        "groww": "groww",
        "zerodha": "zerodha",
        "kite": "zerodha",
    }
    if raw in aliases:
        return aliases[raw]
    presets = load_presets()
    if raw in presets.get("brokers", {}):
        return raw
    return presets.get("default_broker", "indmoney")


def _leg_qty(leg: dict[str, Any]) -> int:
    return int(leg.get("quantity") or leg.get("lot_size", 1) * leg.get("lots", 1))


def _leg_turnover(leg: dict[str, Any]) -> float:
    return float(leg.get("price") or 0) * _leg_qty(leg)


def _is_futures_leg(leg: dict[str, Any]) -> bool:
    seg = str(leg.get("segment") or "").upper()
    if seg == "FUTURE" or seg == "FUT":
        return True
    sym = str(leg.get("symbol") or "").upper()
    return sym.endswith("FUT") and "CE" not in sym and "PE" not in sym


def _statutory_leg_charges(
    turnover: float,
    side: str,
    *,
    segment: str,
) -> dict[str, float]:
    presets = load_presets()
    key = "nse_futures" if segment == "futures" else "nse_options"
    stat = presets["statutory"][key]
    stt = turnover * stat["stt_sell_rate"] if side == "SELL" else 0.0
    exchange = turnover * stat["exchange_rate"]
    sebi = turnover * (stat["sebi_per_crore"] / 1e7)
    stamp = turnover * stat["stamp_buy_rate"] if side == "BUY" else 0.0
    return {
        "stt": stt,
        "exchange": exchange,
        "sebi": sebi,
        "stamp": stamp,
        "gst_rate": stat["gst_rate"],
    }


def calculate_leg_charges(
    leg: dict[str, Any],
    *,
    broker: str | None = None,
) -> dict[str, Any]:
    """Return per-leg charge breakdown for one F&O leg."""
    presets = load_presets()
    broker_id = normalize_broker_id(broker or presets.get("default_broker"))
    broker_cfg = presets["brokers"][broker_id]
    side = str(leg.get("side") or "BUY").upper()
    turnover = _leg_turnover(leg)
    segment = "futures" if _is_futures_leg(leg) else "options"
    brokerage = float(
        broker_cfg["fno_futures_brokerage_inr"]
        if segment == "futures"
        else broker_cfg["fno_options_brokerage_inr"]
    )
    stat = _statutory_leg_charges(turnover, side, segment=segment)
    gst = stat["gst_rate"] * (brokerage + stat["exchange"] + stat["sebi"])
    total = brokerage + stat["stt"] + stat["exchange"] + gst + stat["stamp"] + stat["sebi"]
    return {
        "symbol": leg.get("symbol"),
        "side": side,
        "brokerage": round(brokerage, 2),
        "stt": round(stat["stt"], 2),
        "exchange": round(stat["exchange"], 2),
        "gst": round(gst, 2),
        "stamp": round(stat["stamp"], 2),
        "sebi": round(stat["sebi"], 2),
        "total_charges": round(total, 2),
        "turnover": round(turnover, 2),
        "source": broker_id,
        "segment": segment,
    }


def calculate_charges_for_legs(
    legs: list[dict[str, Any]],
    *,
    broker: str | None = None,
) -> dict[str, Any]:
    """Aggregate charges for multiple legs using broker preset constants."""
    if not legs:
        return {
            "per_leg": [],
            "total": {},
            "broker_preset": normalize_broker_id(broker),
            "charge_source": "presets",
        }
    presets = load_presets()
    broker_id = normalize_broker_id(broker or presets.get("default_broker"))
    per_leg = [calculate_leg_charges(leg, broker=broker_id) for leg in legs]
    totals = {
        "brokerage": 0.0,
        "stt": 0.0,
        "exchange": 0.0,
        "gst": 0.0,
        "stamp": 0.0,
        "sebi": 0.0,
        "total_charges": 0.0,
    }
    for row in per_leg:
        for k in totals:
            totals[k] += float(row.get(k) or 0)
    for k in totals:
        totals[k] = round(totals[k], 2)

    net = 0.0
    for leg in legs:
        sign = 1 if str(leg.get("side") or "BUY").upper() == "SELL" else -1
        net += sign * _leg_turnover(leg)

    return {
        "per_leg": per_leg,
        "total": totals,
        "broker_preset": broker_id,
        "broker_display": presets["brokers"][broker_id]["display_name"],
        "net_debit_credit": round(net, 2),
        "charge_source": "presets",
        "pricing_url": presets["brokers"][broker_id].get("pricing_url"),
    }


def calculate_charges_with_exit_for_legs(
    legs: list[dict[str, Any]],
    *,
    spot: float,
    broker: str | None = None,
) -> dict[str, Any]:
    """Entry charges plus estimated exit STT/exchange on short option assignment."""
    entry = calculate_charges_for_legs(legs, broker=broker)
    if spot <= 0:
        entry["round_trip_charges"] = entry["total"].get("total_charges", 0)
        return entry

    exit_legs: list[dict[str, Any]] = []
    for leg in legs:
        if str(leg.get("side") or "").upper() != "SELL":
            continue
        strike = float(leg.get("strike") or 0)
        opt = str(leg.get("option_type") or "CE").upper()
        qty = _leg_qty(leg)
        intrinsic = max(0.0, spot - strike) if opt == "CE" else max(0.0, strike - spot)
        if intrinsic <= 0:
            continue
        exit_legs.append(
            {
                **leg,
                "side": "SELL",
                "price": intrinsic,
                "quantity": qty,
            }
        )

    exit_total = 0.0
    exit_per: list[dict[str, Any]] = []
    if exit_legs:
        exit_calc = calculate_charges_for_legs(exit_legs, broker=broker)
        exit_per = exit_calc.get("per_leg") or []
        exit_total = float((exit_calc.get("total") or {}).get("total_charges") or 0)

    entry_total = float((entry.get("total") or {}).get("total_charges") or 0)
    entry["exit"] = {"per_leg": exit_per, "total": {"total_charges": round(exit_total, 2)}}
    entry["exit_charges"] = round(exit_total, 2)
    entry["round_trip_charges"] = round(entry_total + exit_total, 2)
    return entry
