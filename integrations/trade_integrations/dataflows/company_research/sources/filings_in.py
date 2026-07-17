"""India corporate filings and announcements — BSE India only."""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone
from typing import Any

from ..market import NormalizedTicker
from ..models import StageResult
from .bse_india import fetch_bse_calendar_events
from .resilience import (
    SourceAttempt,
    classify_error,
    remediation_for,
    stage_errors,
    stage_status_from_attempts,
    _record_source_failure,
)

logger = logging.getLogger(__name__)


def _stage_now() -> datetime:
    return datetime.now(timezone.utc)


def _normalize_filing(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "date": row.get("date") or "",
        "title": row.get("description") or row.get("purpose") or "",
        "type": row.get("type") or "announcement",
        "source": row.get("source") or "",
    }


def _fetch_bse_filings(symbol: str, *, lookback_days: int) -> list[dict[str, Any]]:
    start = date.today() - timedelta(days=lookback_days)
    end = date.today()
    events = fetch_bse_calendar_events(symbol, start=start, end=end)
    return [_normalize_filing(e) for e in events]


def fetch_filings_in(
    normalized: NormalizedTicker,
    *,
    lookback_days: int = 30,
) -> StageResult:
    """Recent corporate announcements and filings from BSE India only."""
    symbol = normalized.base_symbol
    attempts: list[SourceAttempt] = []
    all_filings: list[dict[str, Any]] = []

    try:
        rows = _fetch_bse_filings(symbol, lookback_days=lookback_days)
        if rows:
            all_filings.extend(rows)
            attempts.append(SourceAttempt(name="bse_india", status="ok", data={"filings": rows}))
        else:
            attempts.append(
                _record_source_failure(
                    "bse_india",
                    error="no data",
                    remediation=remediation_for("no_data"),
                    optional=False,
                )
            )
    except Exception as exc:
        attempts.append(
            _record_source_failure(
                "bse_india",
                error=str(exc),
                remediation=remediation_for(classify_error(exc)),
                optional=False,
            )
        )

    seen: set[tuple[str, str]] = set()
    deduped: list[dict[str, Any]] = []
    for row in all_filings:
        key = (row.get("date") or "", row.get("title") or "")
        if key in seen:
            continue
        seen.add(key)
        deduped.append(row)
    deduped.sort(key=lambda r: r.get("date") or "", reverse=True)

    ok_sources = [a.name for a in attempts if a.status == "ok"]
    status = stage_status_from_attempts(attempts, has_output=bool(deduped), stage="filings")

    return StageResult(
        stage="filings",
        status=status,
        vendor="+".join(ok_sources) if ok_sources else "filings_in",
        fetched_at=_stage_now(),
        data={
            "filings": deduped[:20],
            "filing_count": len(deduped),
            "lookback_days": lookback_days,
            "source_attempts": [a.to_dict() for a in attempts],
        },
        errors=stage_errors(attempts, stage="filings"),
    )
