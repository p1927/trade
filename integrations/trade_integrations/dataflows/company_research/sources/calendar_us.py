"""US market calendar — yfinance per-ticker + optional finance-calendars NASDAQ feed."""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone
from typing import Any

from ..market import NormalizedTicker
from ..models import StageResult
from .calendar_in import _date_window, merge_calendar_events
from .resilience import (
    SourceAttempt,
    classify_error,
    remediation_for,
    stage_status_from_attempts,
)

logger = logging.getLogger(__name__)


def _stage_now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_event_date(value: Any) -> date | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value).strip()
    if not text:
        return None
    for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%m/%d/%Y"):
        try:
            return datetime.strptime(text[:10], fmt).date()
        except ValueError:
            continue
    return None


def _event_in_window(event_date: date | None, *, start: date, end: date) -> bool:
    if event_date is None:
        return False
    return start <= event_date <= end


def _fetch_yfinance_calendar(
    symbol: str,
    *,
    start: date,
    end: date,
) -> list[dict[str, Any]]:
    try:
        import yfinance as yf
    except ImportError:
        return []

    payload = yf.Ticker(symbol).calendar
    if not isinstance(payload, dict) or not payload:
        return []

    symbol_upper = symbol.strip().upper()
    events: list[dict[str, Any]] = []
    field_map = (
        ("Earnings Date", "earnings", "Earnings"),
        ("Dividend Date", "dividend", "Dividend"),
        ("Ex-Dividend Date", "ex_dividend", "Ex-dividend"),
    )

    for field, event_type, purpose in field_map:
        raw_values = payload.get(field)
        if raw_values is None:
            continue
        if not isinstance(raw_values, list):
            raw_values = [raw_values]
        for raw in raw_values:
            event_date = _parse_event_date(raw)
            if not _event_in_window(event_date, start=start, end=end):
                continue
            events.append(
                {
                    "symbol": symbol_upper,
                    "company": "",
                    "type": event_type,
                    "purpose": purpose,
                    "description": f"{purpose} scheduled",
                    "date": event_date.isoformat() if event_date else "",
                    "source": "yfinance:calendar",
                }
            )

    return events


def _fetch_finance_calendars(
    symbol: str,
    *,
    start: date,
    end: date,
) -> list[dict[str, Any]]:
    try:
        import finance_calendars as fc
    except ImportError:
        return []

    symbol_upper = symbol.strip().upper()
    events: list[dict[str, Any]] = []
    cursor = start
    while cursor <= end:
        try:
            frame = fc.get_earnings_by_date(datetime.combine(cursor, datetime.min.time()))
        except Exception as exc:
            logger.info("finance_calendars failed for %s: %s", cursor, exc)
            cursor += timedelta(days=1)
            continue
        if frame is None or getattr(frame, "empty", True):
            cursor += timedelta(days=1)
            continue
        symbol_col = None
        for candidate in ("symbol", "Symbol", "ticker"):
            if candidate in frame.columns:
                symbol_col = candidate
                break
        if not symbol_col:
            cursor += timedelta(days=1)
            continue
        subset = frame[frame[symbol_col].astype(str).str.upper() == symbol_upper]
        for _, row in subset.iterrows():
            company = str(row.get("company", "") or row.get("name", "") or "").strip()
            events.append(
                {
                    "symbol": symbol_upper,
                    "company": company,
                    "type": "earnings",
                    "purpose": "Earnings",
                    "description": company or f"{symbol_upper} earnings",
                    "date": cursor.isoformat(),
                    "source": "finance_calendars:earnings",
                }
            )
        cursor += timedelta(days=1)

    return events


def _fetch_calendar_source(name: str, fetcher) -> SourceAttempt:
    try:
        events = fetcher()
    except Exception as exc:
        code = classify_error(exc)
        return SourceAttempt(
            name=name,
            status="error",
            error=str(exc),
            remediation=remediation_for(code),
        )
    if not events:
        return SourceAttempt(
            name=name,
            status="error",
            error="no data",
            remediation=remediation_for("no_data"),
        )
    return SourceAttempt(name=name, status="ok", data={"events": events})


def fetch_calendar_us(
    normalized: NormalizedTicker,
    *,
    lookahead_days: int = 14,
    lookback_days: int = 0,
) -> StageResult:
    """Collect upcoming US earnings and dividend dates for one ticker."""
    start, end = _date_window(lookahead_days, lookback_days=lookback_days)
    symbol = normalized.base_symbol

    source_jobs = [
        (
            "yfinance",
            lambda: _fetch_yfinance_calendar(symbol, start=start, end=end),
        ),
        (
            "finance_calendars",
            lambda: _fetch_finance_calendars(symbol, start=start, end=end),
        ),
    ]
    attempts = [_fetch_calendar_source(name, fn) for name, fn in source_jobs]

    raw_events: list[dict[str, Any]] = []
    for attempt in attempts:
        if attempt.status == "ok":
            raw_events.extend(attempt.data.get("events") or [])

    events = merge_calendar_events(raw_events)
    ok_sources = [a.name for a in attempts if a.status == "ok"]
    vendor = "+".join(ok_sources) if ok_sources else "calendar_us"
    status = stage_status_from_attempts(attempts, has_output=bool(events))

    return StageResult(
        stage="calendar",
        status=status,
        vendor=vendor,
        fetched_at=_stage_now(),
        data={
            "symbol": symbol,
            "from_date": start.isoformat(),
            "to_date": end.isoformat(),
            "lookahead_days": lookahead_days,
            "lookback_days": lookback_days,
            "event_count": len(events),
            "events": events,
            "source_attempts": [a.to_dict() for a in attempts],
        },
        errors=[f"{a.name}: {a.error}" for a in attempts if a.status != "ok" and a.error],
    )
