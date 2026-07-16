"""RBI repo rate step schedule for historical factor backfill."""

from __future__ import annotations

import json
import os
from datetime import date, datetime


# MPC effective dates (inclusive) → repo rate (%). Newest last.
_DEFAULT_REPO_SCHEDULE: list[tuple[str, float]] = [
    ("2019-02-07", 6.25),
    ("2019-06-06", 5.75),
    ("2019-08-07", 5.40),
    ("2019-10-04", 5.15),
    ("2020-05-22", 4.00),
    ("2022-05-04", 4.40),
    ("2022-06-08", 4.90),
    ("2022-08-05", 5.40),
    ("2022-09-30", 5.90),
    ("2023-02-08", 6.50),
]


def _parse_schedule(raw: str) -> list[tuple[str, float]]:
    payload = json.loads(raw)
    if not isinstance(payload, list):
        raise ValueError("RBI_REPO_RATE_HISTORY must be a JSON list")
    schedule: list[tuple[str, float]] = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        effective = str(item.get("effective") or item.get("date") or "")[:10]
        rate = item.get("rate")
        if effective and rate is not None:
            schedule.append((effective, float(rate)))
    if not schedule:
        raise ValueError("RBI_REPO_RATE_HISTORY contained no valid entries")
    return sorted(schedule, key=lambda row: row[0])


def load_repo_schedule() -> list[tuple[str, float]]:
    """Return sorted (effective_date, rate) pairs."""
    env_raw = os.getenv("RBI_REPO_RATE_HISTORY", "").strip()
    if env_raw:
        try:
            return _parse_schedule(env_raw)
        except (json.JSONDecodeError, ValueError, TypeError):
            pass
    return list(_DEFAULT_REPO_SCHEDULE)


def repo_rate_on(day: date | str) -> float:
    """Repo rate in effect on ``day`` from the MPC step schedule."""
    if isinstance(day, str):
        day = date.fromisoformat(day[:10])
    schedule = load_repo_schedule()
    rate = schedule[0][1]
    for effective, value in schedule:
        if day >= date.fromisoformat(effective):
            rate = value
        else:
            break
    return float(rate)
