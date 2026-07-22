"""Map replay bars to OpenAlgo quote payloads."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")


def interpolate_bar_ltp(
    *,
    bar: dict[str, Any],
    sim_ts: datetime,
    bar_minutes: int = 1,
) -> float:
    """Smooth LTP within a bar using elapsed sim time (open → close path)."""
    open_p = float(bar.get("open") or bar.get("close") or 0)
    close_p = float(bar.get("close") or open_p)
    bar_ts_raw = bar.get("bar_ts")
    if not bar_ts_raw or bar_minutes <= 0:
        return close_p

    bar_start = datetime.fromisoformat(str(bar_ts_raw))
    if bar_start.tzinfo is None:
        bar_start = bar_start.replace(tzinfo=IST)
    sim_ist = sim_ts.astimezone(IST)
    elapsed = max(0.0, (sim_ist - bar_start).total_seconds())
    duration = float(bar_minutes * 60)
    if duration <= 0:
        return close_p
    frac = min(1.0, elapsed / duration)
    return open_p + (close_p - open_p) * frac


def to_openalgo_quote(
    *,
    symbol: str,
    exchange: str,
    bar: dict[str, Any],
    sim_ts: datetime,
    bar_minutes: int = 1,
    oi: int | None = None,
    interpolate: bool = True,
) -> dict[str, Any]:
    ltp = (
        interpolate_bar_ltp(bar=bar, sim_ts=sim_ts, bar_minutes=bar_minutes)
        if interpolate
        else float(bar.get("ltp") or bar.get("close") or 0)
    )
    spread = max(0.05, ltp * 0.0001)
    return {
        "bid": round(ltp - spread, 2),
        "ask": round(ltp + spread, 2),
        "open": float(bar.get("open") or ltp),
        "high": float(bar.get("high") or ltp),
        "low": float(bar.get("low") or ltp),
        "ltp": round(ltp, 2),
        "prev_close": float(bar.get("prev_close") or ltp),
        "volume": int(bar.get("volume") or 0),
        "oi": int((bar.get("oi") if oi is None else oi) or 0),
        "source": "stock_simulator",
        "simulated": True,
        "sim_ts": sim_ts.isoformat(),
        "symbol": symbol.upper(),
        "exchange": exchange.upper(),
        "bar_ts": bar.get("bar_ts"),
    }
