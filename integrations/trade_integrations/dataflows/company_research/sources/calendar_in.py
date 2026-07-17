"""India market calendar — multi-source with RSS/news fallback."""

from __future__ import annotations

import logging
import re
from datetime import date, datetime, timedelta, timezone
from typing import Any

from ..config import get_research_config
from ..market import NormalizedTicker
from ..models import StageResult
from .bse_india import fetch_bse_calendar_events
from .moneycontrol_rss import fetch_results_news
from .resilience import (
    SourceAttempt,
    classify_error,
    remediation_for,
    resolve_bse_scrip_code,
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


def _normalize_nselib_row(row: Any, *, source: str) -> dict[str, Any] | None:
    symbol = str(getattr(row, "symbol", "") or row.get("symbol", "")).strip().upper()
    if not symbol:
        return None
    purpose = str(getattr(row, "purpose", "") or row.get("purpose", "") or "").strip()
    description = str(
        getattr(row, "bm_desc", "")
        or row.get("bm_desc", "")
        or getattr(row, "subject", "")
        or row.get("subject", "")
        or purpose
    ).strip()
    event_date = _to_iso_date(getattr(row, "date", None) or row.get("date"))
    event_type = purpose.lower().replace(" ", "_") if purpose else "event"
    return {
        "symbol": symbol,
        "company": str(getattr(row, "company", "") or row.get("company", "")).strip(),
        "type": event_type,
        "purpose": purpose,
        "description": description,
        "date": event_date,
        "source": source,
    }


def _fetch_nselib_events(
    symbol: str,
    *,
    start: date,
    end: date,
) -> tuple[list[dict[str, Any]], list[str]]:
    errors: list[str] = []
    events: list[dict[str, Any]] = []
    try:
        from nselib import capital_market
    except ImportError:
        return [], ["nselib: not installed"]

    start_s = start.strftime("%d-%m-%Y")
    end_s = end.strftime("%d-%m-%Y")
    symbol_upper = symbol.upper()

    for fetch_name, fetcher in (
        ("event_calendar_for_equity", capital_market.event_calendar_for_equity),
        ("corporate_actions_for_equity", capital_market.corporate_actions_for_equity),
    ):
        try:
            frame = fetcher(from_date=start_s, to_date=end_s)
        except Exception as exc:
            errors.append(f"nselib.{fetch_name}: {exc}")
            continue
        if frame is None or getattr(frame, "empty", True):
            errors.append(f"nselib.{fetch_name}: no rows")
            continue
        symbol_col = "symbol" if "symbol" in frame.columns else None
        subset = frame
        if symbol_col:
            subset = frame[frame[symbol_col].astype(str).str.upper() == symbol_upper]
        for _, row in subset.iterrows():
            normalized = _normalize_nselib_row(row, source=f"nselib:{fetch_name}")
            if normalized:
                events.append(normalized)

    return events, errors


_CALENDAR_KEYWORDS = re.compile(
    r"\b(results?|earnings|board meeting|dividend|bonus|split|agm|egm|quarterly|q[1-4])\b",
    re.I,
)


def _fetch_news_calendar_events(
    symbol: str,
    *,
    start: date,
    end: date,
) -> list[dict[str, Any]]:
    """Extract event-like signals from configured India RSS feeds."""
    try:
        from trade_integrations.dataflows.rss_feeds import fetch_rss_feeds
    except ImportError:
        return []

    try:
        block = fetch_rss_feeds(symbol)
    except Exception as exc:
        logger.info("RSS calendar fallback failed for %s: %s", symbol, exc)
        return []

    events: list[dict[str, Any]] = []
    for line in block.splitlines():
        if not line.startswith("- "):
            continue
        title = line[2:].strip()
        if symbol.upper() not in title.upper():
            continue
        if not _CALENDAR_KEYWORDS.search(title):
            continue
        events.append(
            {
                "symbol": symbol.upper(),
                "company": "",
                "type": "news_signal",
                "purpose": "News mention",
                "description": title,
                "date": "",
                "source": "rss_feeds",
            }
        )
    return events


def _fetch_yfinance_earnings_events(normalized: NormalizedTicker) -> list[dict[str, Any]]:
    """Lightweight earnings date from yfinance ticker.calendar."""
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


def _fetch_dalal_bse_calendar(symbol: str) -> list[dict[str, Any]]:
    scrip = resolve_bse_scrip_code(symbol)
    if not scrip:
        return []
    try:
        import dalal  # type: ignore[import-untyped]
    except ImportError:
        return []

    events: list[dict[str, Any]] = []
    try:
        announcements = dalal.announcements(scrip, exchange="BSE") or []
    except Exception as exc:
        logger.info("dalal BSE announcements failed for %s: %s", symbol, exc)
        return events

    for row in announcements[:15]:
        if isinstance(row, dict):
            title = row.get("HEADLINE") or row.get("headline") or row.get("subject") or ""
            event_date = _to_iso_date(row.get("NEWS_DT") or row.get("date"))
        else:
            title = str(row)
            event_date = ""
        if not title:
            continue
        events.append(
            {
                "symbol": symbol.upper(),
                "company": "",
                "type": "bse_announcement",
                "purpose": "Corporate announcement",
                "description": title,
                "date": event_date,
                "source": "dalal_bse:announcements",
            }
        )
    return events


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
    """Collect upcoming Indian corporate events from every available source."""
    if lookback_days is None:
        lookback_days = get_research_config().calendar_lookback_days
    start, end = _date_window(lookahead_days, lookback_days=lookback_days)
    symbol = normalized.base_symbol

    def _bse_india() -> list[dict[str, Any]]:
        return fetch_bse_calendar_events(symbol, start=start, end=end)

    def _yfinance_earnings() -> list[dict[str, Any]]:
        return _fetch_yfinance_earnings_events(normalized)

    def _nselib() -> list[dict[str, Any]]:
        events, _ = _fetch_nselib_events(symbol, start=start, end=end)
        return events

    def _rss() -> list[dict[str, Any]]:
        events = fetch_results_news(symbol)
        if events:
            return events
        return _fetch_news_calendar_events(symbol, start=start, end=end)

    def _dalal_bse() -> list[dict[str, Any]]:
        return _fetch_dalal_bse_calendar(symbol)

    def _tapetide() -> list[dict[str, Any]]:
        return fetch_tapetide_calendar_events(symbol)

    source_jobs: list[tuple[str, Any, bool]] = [
        ("bse_india", _bse_india, False),
        ("yfinance", _yfinance_earnings, False),
        ("nselib", _nselib, True),
        ("moneycontrol_rss", _rss, True),
    ]
    if resolve_bse_scrip_code(symbol):
        source_jobs.append(("dalal_bse", _dalal_bse, True))

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

    if not resolve_bse_scrip_code(symbol):
        attempts.append(
            SourceAttempt(
                name="dalal_bse",
                status="skipped",
                error="bse_code_missing",
                remediation=remediation_for("bse_code_missing"),
            )
        )

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
