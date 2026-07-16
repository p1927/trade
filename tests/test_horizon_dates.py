"""Tests for trading-session horizon maturity."""

from __future__ import annotations

import pytest

from trade_integrations.dataflows.index_research.horizon_dates import (
    resolve_maturity_trading_date,
)


@pytest.mark.unit
def test_resolve_maturity_trading_date_offsets_by_sessions():
    trading = [
        "2026-02-10",
        "2026-02-11",
        "2026-02-12",
        "2026-02-13",
        "2026-02-16",
        "2026-02-17",
        "2026-02-18",
        "2026-02-19",
        "2026-02-20",
        "2026-02-23",
        "2026-02-24",
        "2026-02-25",
        "2026-02-26",
        "2026-02-27",
        "2026-03-02",
        "2026-03-03",
        "2026-03-04",
        "2026-03-05",
        "2026-03-06",
        "2026-03-09",
        "2026-03-10",
    ]
    maturity = resolve_maturity_trading_date("2026-02-17", 14, trading)
    assert maturity == "2026-03-09"


@pytest.mark.unit
def test_resolve_maturity_not_calendar_days():
    trading = ["2026-02-17", "2026-02-18", "2026-02-19", "2026-02-20", "2026-02-23", "2026-02-24"]
    maturity = resolve_maturity_trading_date("2026-02-17", 14, trading)
    assert maturity is None
    assert resolve_maturity_trading_date("2026-02-17", 5, trading) == "2026-02-24"


@pytest.mark.unit
def test_resolve_maturity_unknown_date_uses_last_on_or_before():
    trading = ["2026-02-16", "2026-02-17", "2026-02-18", "2026-02-19"]
    maturity = resolve_maturity_trading_date("2026-02-17", 2, trading)
    assert maturity == "2026-02-19"
