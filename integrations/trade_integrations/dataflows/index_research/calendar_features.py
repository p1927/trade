"""Indian market calendar features for index factor matrix."""

from __future__ import annotations

from datetime import date, timedelta


def last_thursday_of_month(year: int, month: int) -> date:
    """Return the monthly F&O expiry (last Thursday) for ``month``."""
    if month == 12:
        next_month = date(year + 1, 1, 1)
    else:
        next_month = date(year, month + 1, 1)
    cursor = next_month - timedelta(days=1)
    while cursor.weekday() != 3:
        cursor -= timedelta(days=1)
    return cursor


def days_to_monthly_expiry(as_of: date) -> int:
    """Trading days until the next monthly F&O expiry (calendar days proxy)."""
    expiry = last_thursday_of_month(as_of.year, as_of.month)
    if as_of > expiry:
        if as_of.month == 12:
            expiry = last_thursday_of_month(as_of.year + 1, 1)
        else:
            expiry = last_thursday_of_month(as_of.year, as_of.month + 1)
    return max(0, (expiry - as_of).days)


def is_budget_week(as_of: date) -> float:
    """1.0 during Union Budget week (Jan 29 – Feb 4), else 0.0."""
    budget_anchor = date(as_of.year, 2, 1)
    start = budget_anchor - timedelta(days=3)
    end = budget_anchor + timedelta(days=3)
    return 1.0 if start <= as_of <= end else 0.0


def is_results_season(as_of: date) -> float:
    """1.0 during peak Indian earnings seasons (Jan-Feb, Apr-May, Jul-Aug, Oct-Nov)."""
    return 1.0 if as_of.month in {1, 2, 4, 5, 7, 8, 10, 11} else 0.0


def calendar_factor_rows(as_of: date) -> list[dict]:
    """Build factor rows for daily snapshot / live macro enrichment."""
    return [
        {
            "factor": "days_to_monthly_expiry",
            "value": float(days_to_monthly_expiry(as_of)),
            "source": "calendar",
        },
        {
            "factor": "is_budget_week",
            "value": is_budget_week(as_of),
            "source": "calendar",
        },
        {
            "factor": "is_results_season",
            "value": is_results_season(as_of),
            "source": "calendar",
        },
    ]


def calendar_factor_dict(as_of: date) -> dict[str, float]:
    """Flat dict of calendar features for live inference."""
    return {row["factor"]: float(row["value"]) for row in calendar_factor_rows(as_of)}
