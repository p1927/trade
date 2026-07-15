"""Historical volatility context from nselib / yfinance."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from ..market import OptionsInstrument
from ..models import StageResult

logger = logging.getLogger(__name__)


def _stage_now() -> datetime:
    return datetime.now(timezone.utc)


def _realized_vol(closes: list[float], window: int = 20) -> float | None:
    if len(closes) < window + 1:
        return None
    import math

    rets = []
    segment = closes[-(window + 1) :]
    for i in range(1, len(segment)):
        if segment[i - 1] > 0:
            rets.append(math.log(segment[i] / segment[i - 1]))
    if len(rets) < 2:
        return None
    mean = sum(rets) / len(rets)
    var = sum((r - mean) ** 2 for r in rets) / (len(rets) - 1)
    return round(math.sqrt(var) * math.sqrt(252) * 100, 2)


def fetch_analytics_history(instrument: OptionsInstrument) -> StageResult:
    """30-day realized vol for IV/RV comparison (Acelogic-style gate)."""
    now = _stage_now()
    closes: list[float] = []
    vendor = "yfinance"
    symbol = instrument.display_symbol
    yf_symbol = f"{symbol}.NS" if instrument.instrument_type.value == "stock" else "^NSEI"
    if symbol == "BANKNIFTY":
        yf_symbol = "^NSEBANK"
    elif symbol == "NIFTY":
        yf_symbol = "^NSEI"

    try:
        import yfinance as yf

        end = datetime.now().date()
        start = end - timedelta(days=60)
        hist = yf.Ticker(yf_symbol).history(start=start.isoformat(), end=end.isoformat())
        if hist is not None and not hist.empty:
            closes = [float(x) for x in hist["Close"].tolist()]
    except Exception as exc:
        logger.debug("yfinance history failed: %s", exc)

    rv30 = _realized_vol(closes, 30)
    rv10 = _realized_vol(closes, 10)
    return StageResult(
        stage="analytics_history",
        status="ok" if rv30 else "partial",
        vendor=vendor,
        fetched_at=now,
        data={
            "rv30_pct": rv30,
            "rv10_pct": rv10,
            "symbol": yf_symbol,
        },
    )
