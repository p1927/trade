"""Event-driven index scenario builder."""

from __future__ import annotations

from datetime import date, datetime, timedelta

from trade_integrations.dataflows.index_research.attribution import (
    _is_earnings_event,
    _parse_event_date,
)
from trade_integrations.dataflows.index_research.models import ConstituentSignal


def _today() -> date:
    return date.today()


def _count_earnings_within_horizon(
    signals: list[ConstituentSignal],
    *,
    horizon_days: int,
) -> int:
    as_of = _today()
    deadline = as_of + timedelta(days=horizon_days)
    count = 0
    for signal in signals:
        for event in signal.events:
            if not _is_earnings_event(event):
                continue
            event_date = _parse_event_date(event.get("date"))
            if event_date is None or as_of <= event_date <= deadline:
                count += 1
    return count


def _index_range(spot: float, low_pct: float, high_pct: float) -> list[float]:
    return [
        round(spot * (1 + low_pct / 100), 2),
        round(spot * (1 + high_pct / 100), 2),
    ]


def _has_upcoming_rbi(macro_factors: dict, *, horizon_days: int) -> bool:
    events = macro_factors.get("rbi_events") or []
    if not isinstance(events, list):
        events = []
    deadline = _today() + timedelta(days=horizon_days)
    for event in events:
        if not isinstance(event, dict):
            continue
        event_date = event.get("date")
        if event_date is None:
            return True
        if isinstance(event_date, datetime):
            parsed = event_date.date()
        else:
            try:
                parsed = date.fromisoformat(str(event_date)[:10])
            except ValueError:
                continue
        if _today() <= parsed <= deadline:
            return True
    return macro_factors.get("repo_rate") is not None


def build_index_scenarios(
    signals: list[ConstituentSignal],
    macro_factors: dict,
    *,
    spot: float,
    horizon_days: int,
) -> list[dict]:
    """Build 3–6 event scenarios with index ranges anchored to spot."""
    scale = horizon_days / 14.0
    scenarios: list[dict] = []

    earnings_count = _count_earnings_within_horizon(signals, horizon_days=horizon_days)
    if earnings_count >= 2:
        scenarios.append(
            {
                "event": "earnings_cluster",
                "outcome": "positive_surprises",
                "index_range": _index_range(spot, -0.5 * scale, 2.0 * scale),
                "probability": 0.35,
            }
        )
        scenarios.append(
            {
                "event": "earnings_cluster",
                "outcome": "negative_surprises",
                "index_range": _index_range(spot, -2.5 * scale, 0.5 * scale),
                "probability": 0.25,
            }
        )

    if _has_upcoming_rbi(macro_factors, horizon_days=horizon_days):
        scenarios.append(
            {
                "event": "rbi_policy",
                "outcome": "dovish_hold",
                "index_range": _index_range(spot, 0.0 * scale, 1.5 * scale),
                "probability": 0.4,
            }
        )
        scenarios.append(
            {
                "event": "rbi_policy",
                "outcome": "hawkish_surprise",
                "index_range": _index_range(spot, -2.0 * scale, 0.5 * scale),
                "probability": 0.2,
            }
        )

    scenarios.append(
        {
            "event": "monthly_expiry",
            "outcome": "range_bound",
            "index_range": _index_range(spot, -1.0 * scale, 1.0 * scale),
            "probability": 0.45,
        }
    )
    scenarios.append(
        {
            "event": "monthly_expiry",
            "outcome": "breakout",
            "index_range": _index_range(spot, -2.5 * scale, 2.5 * scale),
            "probability": 0.25,
        }
    )

    if len(scenarios) < 3:
        scenarios.append(
            {
                "event": "macro_drift",
                "outcome": "neutral",
                "index_range": _index_range(spot, -1.5 * scale, 1.5 * scale),
                "probability": 0.5,
            }
        )

    return scenarios[:6]
