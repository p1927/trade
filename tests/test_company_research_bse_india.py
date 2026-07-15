"""Unit tests for BSE India API calendar helpers."""

from __future__ import annotations

from datetime import date

import pytest

from trade_integrations.dataflows.company_research.sources.bse_india import (
    _in_window,
    _parse_bse_date,
)


@pytest.mark.unit
class TestBseIndiaHelpers:
    def test_parse_bse_iso_datetime(self):
        assert _parse_bse_date("2026-07-10T17:48:43.49") == "2026-07-10"

    def test_parse_bse_human_date(self):
        assert _parse_bse_date("26 Apr 2001") == "2001-04-26"

    def test_in_window(self):
        start = date(2026, 7, 1)
        end = date(2026, 7, 31)
        assert _in_window("2026-07-10", start=start, end=end)
        assert not _in_window("2026-08-01", start=start, end=end)
