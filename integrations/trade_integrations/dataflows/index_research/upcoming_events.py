"""Extract dated upcoming events for index forecast timeline."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any

from trade_integrations.dataflows.index_research.attribution import (
    _is_earnings_event,
    _parse_event_date,
)
from trade_integrations.dataflows.index_research.models import ConstituentSignal


def _today() -> date:
    return date.today()


def _days_until(event_date: date) -> int:
    return (event_date - _today()).days


def _event_label(event: dict[str, Any], symbol: str) -> str:
    event_type = str(event.get("type") or event.get("event_type") or "event").replace("_", " ")
    title = event.get("title") or event.get("name")
    if title:
        return f"{symbol}: {title}"
    return f"{symbol} {event_type}"


def build_upcoming_events(
    signals: list[ConstituentSignal],
    macro_factors: dict[str, Any],
    *,
    horizon_days: int,
) -> list[dict[str, Any]]:
    """Return sorted upcoming events within the prediction horizon."""
    as_of = _today()
    deadline = as_of + timedelta(days=max(horizon_days, 1))
    rows: list[dict[str, Any]] = []

    for signal in signals:
        for event in signal.events or []:
            event_date = _parse_event_date(event.get("date"))
            if event_date is None or event_date < as_of or event_date > deadline:
                continue
            rows.append(
                {
                    "date": event_date.isoformat(),
                    "days_from_now": _days_until(event_date),
                    "event_type": str(event.get("type") or "corporate"),
                    "label": _event_label(event, signal.symbol),
                    "symbol": signal.symbol,
                    "weight": signal.weight,
                    "sector": signal.sector,
                    "impact": event.get("impact"),
                    "category": "constituent",
                }
            )

    days_to_expiry = macro_factors.get("days_to_monthly_expiry")
    if days_to_expiry is not None:
        try:
            dte = int(float(days_to_expiry))
            if 0 <= dte <= horizon_days:
                expiry_date = as_of + timedelta(days=dte)
                rows.append(
                    {
                        "date": expiry_date.isoformat(),
                        "days_from_now": dte,
                        "event_type": "monthly_expiry",
                        "label": "NIFTY monthly F&O expiry",
                        "category": "derivatives",
                    }
                )
        except (TypeError, ValueError):
            pass

    if float(macro_factors.get("is_budget_week") or 0.0) >= 1.0:
        rows.append(
            {
                "date": as_of.isoformat(),
                "days_from_now": 0,
                "event_type": "union_budget",
                "label": "Union Budget week",
                "category": "macro",
            }
        )

    if float(macro_factors.get("is_results_season") or 0.0) >= 1.0:
        rows.append(
            {
                "date": as_of.isoformat(),
                "days_from_now": 0,
                "event_type": "results_season",
                "label": "Peak earnings season",
                "category": "macro",
            }
        )

    rbi_events = macro_factors.get("rbi_events") or []
    if isinstance(rbi_events, list):
        for event in rbi_events:
            if not isinstance(event, dict):
                continue
            raw_date = event.get("date")
            if raw_date is None:
                continue
            if isinstance(raw_date, datetime):
                event_date = raw_date.date()
            else:
                event_date = _parse_event_date(raw_date)
            if event_date is None or event_date < as_of or event_date > deadline:
                continue
            rows.append(
                {
                    "date": event_date.isoformat(),
                    "days_from_now": _days_until(event_date),
                    "event_type": "rbi_policy",
                    "label": str(event.get("title") or event.get("event") or "RBI policy meeting"),
                    "category": "macro",
                }
            )

    rows.sort(key=lambda row: (row.get("days_from_now", 999), -float(row.get("weight") or 0)))
    return rows[:40]
