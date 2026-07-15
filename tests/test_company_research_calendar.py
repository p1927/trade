"""Unit tests for India calendar enrichment."""

from __future__ import annotations

import pytest

from trade_integrations.dataflows.company_research.sources.calendar_in import (
    merge_calendar_events,
    _to_iso_date,
)


@pytest.mark.unit
class TestCalendarHelpers:
    def test_to_iso_date_dd_mm_yyyy(self):
        assert _to_iso_date("17-07-2026") == "2026-07-17"

    def test_to_iso_date_yyyy_mm_dd(self):
        assert _to_iso_date("2026-07-17") == "2026-07-17"

    def test_merge_dedupes_events(self):
        events = [
            {
                "symbol": "RELIANCE",
                "date": "2026-07-17",
                "type": "financial_results",
                "purpose": "Financial Results",
                "description": "Board meeting",
                "source": "nselib:event_calendar_for_equity",
            },
            {
                "symbol": "RELIANCE",
                "date": "2026-07-17",
                "type": "financial_results",
                "purpose": "Financial Results",
                "description": "Board meeting duplicate",
                "source": "india_corp_actions:get_board_meetings",
            },
            {
                "symbol": "RELIANCE",
                "date": "2026-08-01",
                "type": "dividend",
                "purpose": "Dividend",
                "description": "Interim dividend",
                "source": "nselib:corporate_actions_for_equity",
            },
        ]
        merged = merge_calendar_events(events)
        assert len(merged) == 2
        assert merged[0]["date"] == "2026-07-17"
        assert merged[1]["date"] == "2026-08-01"


@pytest.mark.integration
def test_fetch_calendar_in_reliance_has_event():
    from trade_integrations.dataflows.company_research.market import Market, normalize_ticker
    from trade_integrations.dataflows.company_research.sources.calendar_in import fetch_calendar_in

    normalized = normalize_ticker("RELIANCE", market_hint=Market.IN)
    result = fetch_calendar_in(normalized, lookahead_days=30)
    assert result.stage == "calendar"
    assert result.status in ("ok", "partial")
    events = result.data.get("events") or []
    assert len(events) >= 1
    assert any(e.get("symbol") == "RELIANCE" for e in events)
