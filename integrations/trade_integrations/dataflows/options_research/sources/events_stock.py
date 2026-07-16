"""Load calendar and news events from company research hub."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from trade_integrations.context.hub import load_company_research_json

from ..market import OptionsInstrument
from ..models import StageResult

_EVENT_IMPACT: dict[str, dict[str, str]] = {
    "earnings": {"impact_on_price": "directional", "impact_on_vol": "elevated"},
    "results": {"impact_on_price": "directional", "impact_on_vol": "elevated"},
    "dividend": {"impact_on_price": "down_adjust", "impact_on_vol": "low"},
    "board_meeting": {"impact_on_price": "neutral", "impact_on_vol": "moderate"},
    "agm": {"impact_on_price": "neutral", "impact_on_vol": "low"},
    "split": {"impact_on_price": "reprice", "impact_on_vol": "low"},
    "default": {"impact_on_price": "uncertain", "impact_on_vol": "moderate"},
}


def _stage_now() -> datetime:
    return datetime.now(timezone.utc)


def _enrich_event(event: dict[str, Any]) -> dict[str, Any]:
    event_type = str(event.get("type") or event.get("purpose") or "event").lower()
    key = "default"
    for token in _EVENT_IMPACT:
        if token in event_type:
            key = token
            break
    impacts = _EVENT_IMPACT[key]
    return {
        "date": event.get("date"),
        "type": event.get("type") or event.get("purpose") or "event",
        "description": event.get("description") or event.get("purpose") or "",
        "source": event.get("source") or "company_research",
        "impact_on_price": impacts["impact_on_price"],
        "impact_on_vol": impacts["impact_on_vol"],
    }


def fetch_events_stock(instrument: OptionsInstrument, *, lookahead_days: int) -> StageResult:
    """Reuse cached company research for stock-option event context."""
    now = _stage_now()
    doc = load_company_research_json(instrument.display_symbol)
    if doc is None:
        return StageResult(
            stage="events",
            status="skipped",
            vendor="company_research_hub",
            fetched_at=now,
            data={
                "events": [],
                "reason": (
                    "no company_research cache — run: "
                    f"python scripts/run_company_research.py {instrument.display_symbol}"
                ),
            },
        )

    raw_events = list(doc.calendar_events or [])
    events = [_enrich_event(e) for e in raw_events[:30]]
    news_headlines = []
    for block in (doc.news or {}).get("blocks") or []:
        for row in block.get("headlines") or []:
            title = row.get("title") if isinstance(row, dict) else str(row)
            if title:
                news_headlines.append(title)

    return StageResult(
        stage="events",
        status="ok" if events else "partial",
        vendor="company_research_hub",
        fetched_at=now,
        data={
            "events": events,
            "lookahead_days": lookahead_days,
            "news_headlines": news_headlines[:15],
            "sentiment": doc.sentiment,
        },
    )
