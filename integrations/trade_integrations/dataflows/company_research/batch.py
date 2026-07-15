"""Batch helpers — market-wide India results calendar."""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any


def fetch_upcoming_india_results(*, lookahead_days: int = 7) -> list[dict[str, Any]]:
    """Return all NSE financial-results board meetings in the lookahead window."""
    try:
        from nselib import capital_market
    except ImportError:
        return []

    start = date.today()
    end = start + timedelta(days=max(lookahead_days, 1))
    start_s = start.strftime("%d-%m-%Y")
    end_s = end.strftime("%d-%m-%Y")
    try:
        frame = capital_market.event_calendar_for_equity(from_date=start_s, to_date=end_s)
    except Exception:
        return []
    if frame is None or getattr(frame, "empty", True):
        return []

    events: list[dict[str, Any]] = []
    for _, row in frame.iterrows():
        purpose = str(row.get("purpose", "") or "")
        if "financial results" not in purpose.lower():
            continue
        events.append(
            {
                "symbol": str(row.get("symbol", "")).upper(),
                "company": str(row.get("company", "") or ""),
                "date": str(row.get("date", "") or ""),
                "purpose": purpose,
                "description": str(row.get("bm_desc", "") or purpose),
                "source": "nselib:event_calendar_for_equity",
            }
        )
    events.sort(key=lambda e: e.get("date") or "")
    return events
