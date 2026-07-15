"""US earnings surprise signal — Finverse when installed, else yfinance earnings context."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from ..market import Market, NormalizedTicker
from ..models import StageResult

logger = logging.getLogger(__name__)


def _stage_now() -> datetime:
    return datetime.now(timezone.utc)


def _fetch_yfinance_earnings_context(normalized: NormalizedTicker) -> dict[str, Any] | None:
    try:
        import yfinance as yf
    except ImportError:
        return None
    cal = yf.Ticker(normalized.yfinance_symbol).calendar
    if not isinstance(cal, dict):
        return None
    earnings = cal.get("Earnings Average") or cal.get("Earnings High")
    if earnings is None:
        return None
    return {
        "eps_consensus": earnings,
        "earnings_high": cal.get("Earnings High"),
        "earnings_low": cal.get("Earnings Low"),
        "source": "yfinance:calendar",
        "note": "Finverse beat-probability not installed; showing consensus only.",
    }


def fetch_earnings_signal(normalized: NormalizedTicker, *, market: Market) -> StageResult:
    if market != Market.US:
        return StageResult(
            stage="earnings_signal",
            status="skipped",
            vendor="none",
            fetched_at=_stage_now(),
            data={"reason": "earnings_signal is US-only"},
        )

    payload = _fetch_yfinance_earnings_context(normalized)
    if not payload:
        return StageResult(
            stage="earnings_signal",
            status="error",
            vendor="yfinance",
            fetched_at=_stage_now(),
            data={"symbol": normalized.base_symbol},
            errors=["no earnings consensus data"],
        )
    return StageResult(
        stage="earnings_signal",
        status="partial",
        vendor=payload.get("source", "earnings_signal"),
        fetched_at=_stage_now(),
        data=payload,
    )
