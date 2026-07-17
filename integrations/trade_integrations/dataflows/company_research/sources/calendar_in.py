"""India market calendar — BSE India + yfinance earnings; Tapetide enrichment when configured."""

from __future__ import annotations

import logging
import re
from datetime import date, datetime, timedelta, timezone
from typing import Any

from ..config import get_research_config
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
from .tapetide_in import fetch_tapetide_calendar_events

logger = logging.getLogger(__name__)

_EVENT_KEY_RE = re.compile(r"[^a-z0-9]+")


def _stage_now() -> datetime:
    return datetime.now(timezone.utc)


def _date_window(lookahead_days: int, *, lookback_days: int = 0) -> tuple[date, date]:
    start = date.today() - timedelta(days=max(lookback_days, 0))
    end = date.today() + timedelta(days=max(lookahead_days, 1))
    return start, end


def _to_iso_date(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    text = str(value).strip()
    if not text:
        return ""
    for fmt in ("%d-%m-%Y", "%d-%b-%Y", "%Y-%m-%d", "%d/%m/%Y"):
        try:
            return datetime.strptime(text, fmt).date().isoformat()
        except ValueError:
            continue
    return text


def _event_key(event: dict[str, Any]) -> str:
    parts = [
        event.get("symbol", ""),
        event.get("date", ""),
        event.get("type", ""),
        event.get("purpose", ""),
    ]
    normalized = _EVENT_KEY_RE.sub("", " ".join(str(p).lower() for p in parts))
    return normalized


def merge_calendar_events(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Dedupe calendar rows and sort by date."""
    seen: set[str] = set()
    merged: list[dict[str, Any]] = []
    for event in events:
        key = _event_key(event)
        if not key or key in seen:
            continue
        seen.add(key)
        merged.append(event)
    merged.sort(key=lambda row: row.get("date") or "")
    return merged


def _fetch_yfinance_earnings_events(normalized: NormalizedTicker) -> list[dict[str, Any]]:
    """Earnings date from yfinance ticker.calendar."""
    try:
        import yfinance as yf
    except ImportError:
        return []

    try:
        calendar = yf.Ticker(normalized.yfinance_symbol).calendar
    except Exception as exc:
        logger.info("yfinance calendar failed for %s: %s", normalized.base_symbol, exc)
        return []

    earnings_date: Any = None
    if isinstance(calendar, dict):
        earnings_date = calendar.get("Earnings Date")
    elif calendar is not None and hasattr(calendar, "get"):
        earnings_date = calendar.get("Earnings Date")

    if earnings_date is None:
        return []

    if isinstance(earnings_date, (list, tuple)):
        if not earnings_date:
            return []
        earnings_date = earnings_date[0]

    event_date = _to_iso_date(earnings_date)
    if not event_date:
        return []

    return [
        {
            "symbol": normalized.base_symbol.upper(),
            "company": "",
            "type": "earnings",
            "purpose": "Earnings date",
            "description": f"Earnings date (yfinance): {event_date}",
            "date": event_date,
            "source": "yfinance:calendar",
        }
    ]


def _fetch_calendar_source(
    name: str,
    fetcher,
    *,
    optional: bool = False,
) -> SourceAttempt:
    try:
        events = fetcher()
    except Exception as exc:
        code = classify_error(exc)
        return _record_source_failure(
            name,
            error=str(exc),
            remediation=remediation_for(code),
            optional=optional,
        )
    if not events:
        return _record_source_failure(
            name,
            error="no data",
            remediation=remediation_for("no_data"),
            optional=optional,
        )
    return SourceAttempt(name=name, status="ok", data={"events": events})


def fetch_calendar_in(
    normalized: NormalizedTicker,
    *,
    lookahead_days: int = 14,
    lookback_days: int | None = None,
) -> StageResult:
    """Collect upcoming events from reliable sources only (BSE + yfinance; Tapetide if configured)."""
    if lookback_days is None:
        lookback_days = get_research_config().calendar_lookback_days
    start, end = _date_window(lookahead_days, lookback_days=lookback_days)
    symbol = normalized.base_symbol

    def _bse_india() -> list[dict[str, Any]]:
        return fetch_bse_calendar_events(symbol, start=start, end=end)

    def _yfinance_earnings() -> list[dict[str, Any]]:
        return _fetch_yfinance_earnings_events(normalized)

    def _tapetide() -> list[dict[str, Any]]:
        return fetch_tapetide_calendar_events(symbol)

    source_jobs: list[tuple[str, Any, bool]] = [
        ("bse_india", _bse_india, False),
        ("yfinance", _yfinance_earnings, False),
    ]

    attempts = [
        _fetch_calendar_source(name, fn, optional=is_optional)
        for name, fn, is_optional in source_jobs
    ]

    raw_events: list[dict[str, Any]] = []
    for attempt in attempts:
        if attempt.status == "ok":
            raw_events.extend(attempt.data.get("events") or [])

    from trade_integrations.clients.tapetide import is_configured as tapetide_configured

    if tapetide_configured():
        tapetide_attempt = _fetch_calendar_source("tapetide", _tapetide, optional=True)
        attempts.append(tapetide_attempt)
        if tapetide_attempt.status == "ok":
            raw_events.extend(tapetide_attempt.data.get("events") or [])

    events = merge_calendar_events(raw_events)
    ok_sources = [a.name for a in attempts if a.status == "ok"]
    vendor = "+".join(ok_sources) if ok_sources else "calendar_in"
    status = stage_status_from_attempts(attempts, has_output=bool(events), stage="calendar")

    live_quote: dict[str, Any] = {}
    try:
        from trade_integrations.dataflows.market_quotes import fetch_live_quote

        live_quote = fetch_live_quote(symbol) or {}
    except Exception:
        pass

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
            "live_quote": live_quote,
            "source_attempts": [a.to_dict() for a in attempts],
        },
        errors=stage_errors(attempts, stage="calendar"),
    )
