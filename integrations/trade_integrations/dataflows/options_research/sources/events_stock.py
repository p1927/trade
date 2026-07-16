"""Load calendar and research signals from company research hub."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from trade_integrations.context.hub import load_company_research_json
from trade_integrations.dataflows.company_research.signals_bridge import (
    hub_signals_to_events,
    prediction_signals_from_hub,
)

from ..market import OptionsInstrument
from ..models import StageResult

_EVENT_IMPACT: dict[str, dict[str, str]] = {
    "earnings": {"impact_on_price": "directional", "impact_on_vol": "elevated"},
    "results": {"impact_on_price": "directional", "impact_on_vol": "elevated"},
    "earnings_signal": {"impact_on_price": "directional", "impact_on_vol": "elevated"},
    "corp_event_forecast": {"impact_on_price": "uncertain", "impact_on_vol": "elevated"},
    "corp_event_watch": {"impact_on_price": "uncertain", "impact_on_vol": "moderate"},
    "dividend": {"impact_on_price": "down_adjust", "impact_on_vol": "low"},
    "board_meeting": {"impact_on_price": "neutral", "impact_on_vol": "moderate"},
    "agm": {"impact_on_price": "neutral", "impact_on_vol": "low"},
    "split": {"impact_on_price": "reprice", "impact_on_vol": "low"},
    "default": {"impact_on_price": "uncertain", "impact_on_vol": "moderate"},
}


def _stage_now() -> datetime:
    return datetime.now(timezone.utc)


def _enrich_event(event: dict[str, Any]) -> dict[str, Any]:
    if event.get("impact_on_price") and event.get("impact_on_vol"):
        return dict(event)
    event_type = str(event.get("type") or event.get("purpose") or "event").lower()
    key = "default"
    for token in _EVENT_IMPACT:
        if token in event_type:
            key = token
            break
    impacts = _EVENT_IMPACT[key]
    enriched = dict(event)
    enriched.setdefault("impact_on_price", impacts["impact_on_price"])
    enriched.setdefault("impact_on_vol", impacts["impact_on_vol"])
    return enriched


def fetch_events_stock(instrument: OptionsInstrument, *, lookahead_days: int) -> StageResult:
    """Reuse cached company research: calendar, sentiment, Finverse, ED-ALPHA."""
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
                "prediction_signals": {},
                "reason": (
                    "no company_research cache — run: "
                    f"python scripts/run_company_research.py {instrument.display_symbol}"
                ),
            },
        )

    raw_calendar = list(doc.calendar_events or [])
    earnings_signal = dict(doc.earnings_signal or {})
    corp_events = dict(doc.corp_events or {})
    merged_raw = hub_signals_to_events(
        calendar_events=raw_calendar,
        earnings_signal=earnings_signal or None,
        corp_events=corp_events or None,
    )
    events = [_enrich_event(e) for e in merged_raw[:30]]
    prediction_signals = prediction_signals_from_hub(
        earnings_signal=earnings_signal or None,
        corp_events=corp_events or None,
    )

    news_headlines = []
    for block in (doc.news or {}).get("blocks") or []:
        for row in block.get("headlines") or []:
            title = row.get("title") if isinstance(row, dict) else str(row)
            if title:
                news_headlines.append(title)

    has_signals = bool(earnings_signal.get("beat_probability") or corp_events.get("status"))
    status = "ok" if events else "partial"
    if has_signals and status == "partial":
        status = "ok"

    return StageResult(
        stage="events",
        status=status,
        vendor="company_research_hub",
        fetched_at=now,
        data={
            "events": events,
            "lookahead_days": lookahead_days,
            "news_headlines": news_headlines[:15],
            "sentiment": doc.sentiment,
            "earnings_signal": earnings_signal,
            "corp_events": corp_events,
            "prediction_signals": prediction_signals,
        },
    )
