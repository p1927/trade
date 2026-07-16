"""Tests for day-level Nifty attribution."""

from __future__ import annotations

import pytest

from trade_integrations.dataflows.index_research.day_attribution import (
    build_nifty_price_series,
    explain_nifty_day,
)


@pytest.mark.unit
def test_build_nifty_price_series_returns_rows():
    rows = build_nifty_price_series(days=30)
    if not rows:
        pytest.skip("no aligned history in test env")
    assert "date" in rows[0]
    assert "close" in rows[0]


@pytest.mark.unit
def test_explain_nifty_day_for_known_date():
    rows = build_nifty_price_series(days=60)
    if len(rows) < 2:
        pytest.skip("insufficient history")
    payload = explain_nifty_day(rows[-1]["date"], history_days=60)
    assert payload.get("status") in ("ok", "not_found", "error")
    if payload.get("status") == "ok":
        assert payload.get("factor_drivers") is not None
