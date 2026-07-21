"""Regression tests for NiftyInvest flow month filtering."""

from __future__ import annotations

import pytest


@pytest.mark.unit
def test_months_for_range_accepts_niftyinvest_july_format():
    from trade_integrations.dataflows.index_research.sources.web_flow_fetch import (
        _months_for_range,
        _normalize_year_month,
    )

    assert _normalize_year_month("2026-Jul") == (2026, 7)
    assert _normalize_year_month("2026-07") == (2026, 7)

    months = ["2026-Jun", "2026-Jul", "2026-Aug"]
    filtered = _months_for_range(months, start="2026-07-07", end="2026-07-21")
    assert filtered == ["2026-Jul"]


@pytest.mark.unit
def test_months_for_range_spans_multiple_months():
    from trade_integrations.dataflows.index_research.sources.web_flow_fetch import _months_for_range

    months = ["2026-May", "2026-Jun", "2026-Jul", "2026-Aug"]
    filtered = _months_for_range(months, start="2026-06-15", end="2026-07-10")
    assert filtered == ["2026-Jun", "2026-Jul"]
