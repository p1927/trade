"""Unit tests for Indian market calendar features."""

from __future__ import annotations

from datetime import date

import pytest

from trade_integrations.dataflows.index_research.calendar_features import (
    calendar_factor_dict,
    days_to_monthly_expiry,
    is_budget_week,
    is_results_season,
    last_thursday_of_month,
)


@pytest.mark.unit
def test_last_thursday_of_month_july_2026():
    assert last_thursday_of_month(2026, 7) == date(2026, 7, 30)


@pytest.mark.unit
def test_days_to_monthly_expiry_before_expiry():
    as_of = date(2026, 7, 16)
    days = days_to_monthly_expiry(as_of)
    assert days == (date(2026, 7, 30) - as_of).days


@pytest.mark.unit
def test_is_budget_week_around_feb_first():
    assert is_budget_week(date(2026, 2, 1)) == 1.0
    assert is_budget_week(date(2026, 7, 16)) == 0.0


@pytest.mark.unit
def test_is_results_season_peak_months():
    assert is_results_season(date(2026, 4, 15)) == 1.0
    assert is_results_season(date(2026, 3, 15)) == 0.0


@pytest.mark.unit
def test_calendar_factor_dict_keys():
    factors = calendar_factor_dict(date(2026, 7, 16))
    assert set(factors) == {
        "days_to_monthly_expiry",
        "is_budget_week",
        "is_results_season",
    }
