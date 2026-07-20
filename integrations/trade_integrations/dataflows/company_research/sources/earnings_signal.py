"""US earnings surprise signal — Finverse beat-probability when installed, else yfinance consensus."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from ..market import Market, NormalizedTicker
from ..models import StageResult

logger = logging.getLogger(__name__)


def _stage_now() -> datetime:
    return datetime.now(timezone.utc)


def _fetch_yfinance_earnings_context(normalized: NormalizedTicker) -> dict[str, Any]:
    try:
        import yfinance as yf
    except ImportError:
        return {}
    cal = yf.Ticker(normalized.yfinance_symbol).calendar
    if not isinstance(cal, dict):
        return {}
    earnings = cal.get("Earnings Average") or cal.get("Earnings High")
    if earnings is None:
        return {}
    return {
        "eps_consensus": earnings,
        "earnings_high": cal.get("Earnings High"),
        "earnings_low": cal.get("Earnings Low"),
        "consensus_source": "yfinance:calendar",
    }


def _fetch_finverse_beat_probability(normalized: NormalizedTicker) -> dict[str, Any] | None:
    try:
        from finverse.ml.earnings_surprise import analyze
        from finverse.pull import ticker as pull_ticker
    except ImportError:
        return None

    symbol = normalized.yfinance_symbol or normalized.base_symbol
    try:
        data = pull_ticker(symbol)
        result = analyze(data)
    except Exception as exc:
        logger.info("finverse earnings_surprise failed for %s: %s", symbol, exc)
        return None

    payload: dict[str, Any] = {
        "beat_probability": round(float(result.beat_probability), 4),
        "miss_probability": round(float(result.miss_probability), 4),
        "historical_beat_rate": round(float(result.historical_beat_rate), 4),
        "confidence": getattr(result, "confidence", None),
        "revision_momentum": getattr(result, "revision_momentum", None),
        "sector_percentile": getattr(result, "sector_percentile", None),
        "source": "finverse:earnings_surprise",
    }
    eq_score = getattr(result, "earnings_quality_score", None)
    if eq_score is not None:
        payload["earnings_quality_score"] = eq_score
    return payload


def fetch_earnings_signal(normalized: NormalizedTicker, *, market: Market) -> StageResult:
    payload: dict[str, Any] = {"symbol": normalized.base_symbol, "market": market.value}

    if market == Market.IN:
        yf_ctx = _fetch_yfinance_earnings_context(normalized)
        if yf_ctx:
            payload.update(yf_ctx)
            return StageResult(
                stage="earnings_signal",
                status="partial",
                vendor=yf_ctx.get("consensus_source", "yfinance:calendar"),
                fetched_at=_stage_now(),
                data=payload,
            )
        return StageResult(
            stage="earnings_signal",
            status="skipped",
            vendor="yfinance",
            fetched_at=_stage_now(),
            data={**payload, "reason": "no India earnings calendar in yfinance"},
        )

    if market != Market.US:
        return StageResult(
            stage="earnings_signal",
            status="skipped",
            vendor="none",
            fetched_at=_stage_now(),
            data={**payload, "reason": f"earnings_signal unsupported for {market.value}"},
        )

    payload.update(_fetch_yfinance_earnings_context(normalized))

    finverse = _fetch_finverse_beat_probability(normalized)
    if finverse:
        payload.update(finverse)
        vendor = finverse.get("source", "finverse:earnings_surprise")
        status = "ok"
    elif payload.get("eps_consensus") is not None:
        vendor = payload.get("consensus_source", "yfinance:calendar")
        status = "partial"
        payload["note"] = (
            "Finverse not installed; showing yfinance consensus only. "
            "Install: pip install 'trade-stack[research-plus]'"
        )
    else:
        return StageResult(
            stage="earnings_signal",
            status="error",
            vendor="yfinance",
            fetched_at=_stage_now(),
            data={"symbol": normalized.base_symbol},
            errors=["no earnings consensus or finverse data"],
        )

    return StageResult(
        stage="earnings_signal",
        status=status,
        vendor=vendor,
        fetched_at=_stage_now(),
        data=payload,
    )
