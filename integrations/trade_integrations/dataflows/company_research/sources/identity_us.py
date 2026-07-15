"""US market identity — yfinance profile."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from ..market import NormalizedTicker
from ..models import StageResult
from .resilience import SourceAttempt, remediation_for, run_sources, stage_status_from_attempts


def _stage_now() -> datetime:
    return datetime.now(timezone.utc)


def _fetch_yfinance_identity(normalized: NormalizedTicker) -> dict[str, Any] | None:
    try:
        import yfinance as yf
    except ImportError:
        return None
    info = yf.Ticker(normalized.yfinance_symbol).info or {}
    if not info:
        return None
    return {
        "name": info.get("longName") or info.get("shortName") or normalized.base_symbol,
        "sector": info.get("sector") or "",
        "industry": info.get("industry") or "",
        "exchange": info.get("exchange") or info.get("quoteType") or "US",
        "last_price": info.get("currentPrice") or info.get("regularMarketPrice"),
        "market_cap": info.get("marketCap"),
        "currency": info.get("currency") or "USD",
        "source": "yfinance",
    }


def fetch_identity_us(normalized: NormalizedTicker) -> StageResult:
    attempts = run_sources([("yfinance", lambda: _fetch_yfinance_identity(normalized))])
    merged: dict[str, Any] = {
        "base_symbol": normalized.base_symbol,
        "yfinance_symbol": normalized.yfinance_symbol,
    }
    for attempt in attempts:
        if attempt.status == "ok" and attempt.data:
            merged.update(attempt.data)
    ok = [a.name for a in attempts if a.status == "ok"]
    return StageResult(
        stage="identity",
        status=stage_status_from_attempts(attempts, has_output=bool(merged.get("name"))),
        vendor="+".join(ok) if ok else "identity_us",
        fetched_at=_stage_now(),
        data={**merged, "source_attempts": [a.to_dict() for a in attempts]},
        errors=[f"{a.name}: {a.error}" for a in attempts if a.status != "ok" and a.error],
    )
