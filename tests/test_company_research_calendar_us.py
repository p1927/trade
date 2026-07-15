"""Unit tests for calendar date windows and US calendar helpers."""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from trade_integrations.dataflows.company_research.sources.calendar_in import _date_window
from trade_integrations.dataflows.company_research.sources.calendar_us import (
    _event_in_window,
    _parse_event_date,
)


@pytest.mark.unit
class TestCalendarDateWindow:
    def test_lookahead_only(self):
        start, end = _date_window(14, lookback_days=0)
        assert start == date.today()
        assert end == date.today() + timedelta(days=14)

    def test_includes_lookback(self):
        start, end = _date_window(14, lookback_days=7)
        assert start == date.today() - timedelta(days=7)
        assert end == date.today() + timedelta(days=14)


@pytest.mark.unit
class TestCalendarUsHelpers:
    def test_parse_iso_date(self):
        assert _parse_event_date("2026-07-30") == date(2026, 7, 30)

    def test_event_in_window(self):
        start = date(2026, 7, 1)
        end = date(2026, 7, 31)
        assert _event_in_window(date(2026, 7, 15), start=start, end=end)
        assert not _event_in_window(date(2026, 8, 1), start=start, end=end)
