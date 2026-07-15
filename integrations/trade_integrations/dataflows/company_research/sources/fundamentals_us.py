"""US fundamentals — yfinance metrics."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from ..market import NormalizedTicker
from ..models import StageResult
from .resilience import run_sources, stage_status_from_attempts


def _stage_now() -> datetime:
    return datetime.now(timezone.utc)


def _fetch_yfinance_fundamentals(normalized: NormalizedTicker) -> dict[str, Any] | None:
    try:
        import yfinance as yf
    except ImportError:
        return None
    info = yf.Ticker(normalized.yfinance_symbol).info or {}
    if not info:
        return None
    keys = (
        "trailingPE",
        "forwardPE",
        "profitMargins",
        "operatingMargins",
        "revenueGrowth",
        "earningsGrowth",
        "totalRevenue",
        "ebitda",
        "returnOnEquity",
        "debtToEquity",
    )
    metrics = {k: info.get(k) for k in keys if info.get(k) is not None}
    if not metrics:
        return None
    return {"metrics": metrics, "source": "yfinance:info"}


def fetch_fundamentals_us(normalized: NormalizedTicker) -> StageResult:
    attempts = run_sources([("yfinance", lambda: _fetch_yfinance_fundamentals(normalized))])
    merged: dict[str, Any] = {"symbol": normalized.base_symbol}
    for attempt in attempts:
        if attempt.status == "ok" and attempt.data:
            merged.update(attempt.data)
    ok = [a.name for a in attempts if a.status == "ok"]
    return StageResult(
        stage="fundamentals",
        status=stage_status_from_attempts(attempts, has_output=bool(ok)),
        vendor="+".join(ok) if ok else "fundamentals_us",
        fetched_at=_stage_now(),
        data={**merged, "source_attempts": [a.to_dict() for a in attempts]},
        errors=[f"{a.name}: {a.error}" for a in attempts if a.status != "ok" and a.error],
    )
