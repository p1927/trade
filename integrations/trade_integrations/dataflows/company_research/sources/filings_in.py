"""India corporate filings and announcements."""

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
    resolve_bse_scrip_code,
    stage_status_from_attempts,
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


def _fetch_dalal_bse_filings(symbol: str) -> list[dict[str, Any]]:
    scrip = resolve_bse_scrip_code(symbol)
    if not scrip:
        return []
    try:
        import dalal  # type: ignore[import-untyped]
    except ImportError:
        return []

    filings: list[dict[str, Any]] = []
    try:
        rows = dalal.announcements(scrip, exchange="BSE") or []
    except Exception as exc:
        logger.info("dalal announcements failed for %s: %s", symbol, exc)
        return filings

    for row in rows[:15]:
        if isinstance(row, dict):
            title = row.get("HEADLINE") or row.get("headline") or row.get("subject") or ""
            event_date = row.get("NEWS_DT") or row.get("date") or ""
        else:
            title = str(row)
            event_date = ""
        if not title:
            continue
        filings.append(
            _normalize_filing(
                {
                    "date": str(event_date)[:10] if event_date else "",
                    "description": title,
                    "type": "bse_announcement",
                    "source": "dalal_bse:announcements",
                }
            )
        )
    return filings


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
    """Recent corporate announcements and filings for India equities."""
    symbol = normalized.base_symbol
    attempts: list[SourceAttempt] = []
    all_filings: list[dict[str, Any]] = []

    for name, fetcher in (
        ("bse_india", lambda: _fetch_bse_filings(symbol, lookback_days=lookback_days)),
        ("dalal_bse", lambda: _fetch_dalal_bse_filings(symbol)),
    ):
        try:
            rows = fetcher()
            if rows:
                all_filings.extend(rows)
                attempts.append(SourceAttempt(name=name, status="ok", data={"filings": rows}))
            else:
                attempts.append(
                    SourceAttempt(
                        name=name,
                        status="error",
                        error="no data",
                        remediation=remediation_for("no_data"),
                    )
                )
        except Exception as exc:
            attempts.append(
                SourceAttempt(
                    name=name,
                    status="error",
                    error=str(exc),
                    remediation=remediation_for(classify_error(exc)),
                )
            )

    if not resolve_bse_scrip_code(symbol):
        attempts.append(
            SourceAttempt(
                name="dalal_bse",
                status="skipped",
                error="bse_code_missing",
                remediation=remediation_for("bse_code_missing"),
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
    status = stage_status_from_attempts(attempts, has_output=bool(deduped))

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
        errors=[f"{a.name}: {a.error}" for a in attempts if a.status != "ok" and a.error],
    )
