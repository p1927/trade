"""BSE India API — corporate announcements and actions (BSE bypass, no NSE cookies)."""

from __future__ import annotations

import logging
import re
from datetime import date, datetime
from typing import Any

from .resilience import resolve_bse_scrip_code

logger = logging.getLogger(__name__)

_CALENDAR_KEYWORDS = re.compile(
    r"\b(results?|earnings|board meeting|dividend|bonus|split|agm|egm|quarterly|q[1-4])\b",
    re.I,
)


def _parse_bse_date(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    text = str(value).strip()
    if not text or text == "-":
        return ""
    for fmt in (
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%dT%H:%M:%S.%f",
        "%d %b %Y",
        "%d-%m-%Y",
        "%Y-%m-%d",
    ):
        try:
            return datetime.strptime(text[:26], fmt).date().isoformat()
        except ValueError:
            continue
    if len(text) == 8 and text.isdigit():
        try:
            return datetime.strptime(text, "%Y%m%d").date().isoformat()
        except ValueError:
            pass
    return text


def _in_window(event_date: str, *, start: date, end: date) -> bool:
    if not event_date:
        return True
    try:
        parsed = datetime.strptime(event_date[:10], "%Y-%m-%d").date()
    except ValueError:
        return True
    return start <= parsed <= end


def _announcement_matches_calendar(row: dict[str, Any]) -> bool:
    title = str(row.get("NEWSSUB") or row.get("HEADLINE") or "").strip()
    more = str(row.get("MORE") or "").strip()
    text = f"{title} {more}".strip()
    return bool(text and _CALENDAR_KEYWORDS.search(text))


def fetch_bse_calendar_events(
    symbol: str,
    *,
    start: date,
    end: date,
) -> list[dict[str, Any]]:
    """Fetch BSE announcements and corporate actions for one Indian equity."""
    try:
        from bse import BSE
    except ImportError:
        return []

    symbol_upper = symbol.strip().upper()
    scrip = resolve_bse_scrip_code(symbol_upper)
    events: list[dict[str, Any]] = []

    try:
        with BSE("./") as client:
            if not scrip:
                try:
                    scrip = client.getScripCode(symbol_upper)
                except Exception as exc:
                    logger.info("BSE scrip lookup failed for %s: %s", symbol_upper, exc)
                    return []

            if not scrip:
                return []

            start_dt = datetime.combine(start, datetime.min.time())
            end_dt = datetime.combine(end, datetime.max.time())

            try:
                payload = client.announcements(
                    page_no=1,
                    from_date=start_dt,
                    to_date=end_dt,
                    segment="equity",
                    scripcode=str(scrip),
                )
            except Exception as exc:
                logger.info("BSE announcements failed for %s: %s", symbol_upper, exc)
                payload = {}

            for row in payload.get("Table") or []:
                if not isinstance(row, dict):
                    continue
                if not _announcement_matches_calendar(row):
                    continue
                title = str(row.get("NEWSSUB") or row.get("HEADLINE") or row.get("MORE") or "").strip()
                if not title:
                    continue
                event_date = _parse_bse_date(row.get("NEWS_DT") or row.get("DT_TM"))
                if not _in_window(event_date, start=start, end=end):
                    continue
                events.append(
                    {
                        "symbol": symbol_upper,
                        "company": str(row.get("SLONGNAME") or row.get("LONG_NAME") or "").strip(),
                        "type": "bse_announcement",
                        "purpose": "BSE corporate announcement",
                        "description": title,
                        "date": event_date,
                        "source": "bse_india:announcements",
                    }
                )

            try:
                actions = client.actions(scripcode=str(scrip)) or []
            except Exception as exc:
                logger.info("BSE actions failed for %s: %s", symbol_upper, exc)
                actions = []

            for row in actions:
                if not isinstance(row, dict):
                    continue
                row_scrip = str(row.get("scrip_code") or row.get("SCRIP_CD") or "").strip()
                if row_scrip and row_scrip != str(scrip).strip():
                    continue
                purpose = str(row.get("Purpose") or "").strip()
                if not purpose:
                    continue
                event_date = _parse_bse_date(row.get("Ex_date") or row.get("exdate"))
                if event_date and not _in_window(event_date, start=start, end=end):
                    continue
                events.append(
                    {
                        "symbol": symbol_upper,
                        "company": str(row.get("long_name") or row.get("short_name") or "").strip(),
                        "type": "corporate_action",
                        "purpose": purpose,
                        "description": purpose,
                        "date": event_date,
                        "source": "bse_india:actions",
                    }
                )
    except Exception as exc:
        logger.warning("BSE India API calendar failed for %s: %s", symbol_upper, exc)
        return []

    return events
