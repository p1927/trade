"""Map replay bars to OpenAlgo quote payloads."""

from __future__ import annotations

from datetime import datetime
from typing import Any


def to_openalgo_quote(
    *,
    symbol: str,
    exchange: str,
    bar: dict[str, Any],
    sim_ts: datetime,
) -> dict[str, Any]:
    ltp = float(bar.get("ltp") or bar.get("close") or 0)
    spread = max(0.05, ltp * 0.0001)
    return {
        "bid": round(ltp - spread, 2),
        "ask": round(ltp + spread, 2),
        "open": float(bar.get("open") or ltp),
        "high": float(bar.get("high") or ltp),
        "low": float(bar.get("low") or ltp),
        "ltp": ltp,
        "prev_close": float(bar.get("prev_close") or ltp),
        "volume": int(bar.get("volume") or 0),
        "oi": 0,
        "source": "stock_simulator",
        "simulated": True,
        "sim_ts": sim_ts.isoformat(),
        "symbol": symbol.upper(),
        "exchange": exchange.upper(),
        "bar_ts": bar.get("bar_ts"),
    }
