"""US earnings surprise signal for stock options (Finverse, guarded)."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from trade_integrations.dataflows.company_research.market import Market, normalize_ticker

from ..models import StageResult

logger = logging.getLogger(__name__)


def _stage_now() -> datetime:
    return datetime.now(timezone.utc)


def _fetch_finverse(symbol: str) -> dict[str, Any] | None:
    try:
        from finverse.ml.earnings_surprise import analyze
        from finverse.pull import ticker as pull_ticker
    except ImportError:
        return None
    try:
        data = pull_ticker(symbol)
        result = analyze(data)
    except Exception as exc:
        logger.debug("finverse earnings for %s failed: %s", symbol, exc)
        return None
    return {
        "beat_probability": round(float(result.beat_probability), 4),
        "miss_probability": round(float(result.miss_probability), 4),
        "historical_beat_rate": round(float(result.historical_beat_rate), 4),
        "source": "finverse:earnings_surprise",
    }


def fetch_earnings_us_stage(ticker: str) -> StageResult:
    """Optional US earnings context for dual-listed / US ticker options research."""
    now = _stage_now()
    normalized = normalize_ticker(ticker)
    if normalized.market != Market.US:
        return StageResult(
            stage="earnings_us",
            status="skipped",
            vendor="finverse",
            fetched_at=now,
            data={"reason": "India-only underlying — earnings_us applies to US tickers"},
        )
    symbol = normalized.yfinance_symbol or normalized.base_symbol
    payload = _fetch_finverse(symbol)
    if not payload:
        return StageResult(
            stage="earnings_us",
            status="skipped",
            vendor="finverse",
            fetched_at=now,
            data={"reason": "finverse not installed or no US earnings data"},
        )
    return StageResult(
        stage="earnings_us",
        status="ok",
        vendor="finverse",
        fetched_at=now,
        data=payload,
    )
