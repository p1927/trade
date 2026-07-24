"""Resolve replay dates for last-week rotation mode."""

from __future__ import annotations

from pathlib import Path

from trade_integrations.stock_simulator.catalog import ReplayCatalog

_DEFAULT_SYMBOL = "NIFTY"
_DEFAULT_EXCHANGE = "NSE_INDEX"


def latest_trading_days(
    data_root: Path,
    n: int = 5,
    *,
    symbol: str = _DEFAULT_SYMBOL,
    exchange: str = _DEFAULT_EXCHANGE,
) -> list[str]:
    """Return the n most recent trading days available in local replay data."""
    n = max(1, int(n))
    catalog = ReplayCatalog(data_root)
    dates = catalog.available_dates(symbol, exchange)
    if not dates:
        return []
    return dates[-n:] if len(dates) >= n else list(dates)


def resolve_week_replay_date(
    data_root: Path,
    explicit: str | None,
    *,
    n: int = 5,
    symbol: str = _DEFAULT_SYMBOL,
    exchange: str = _DEFAULT_EXCHANGE,
) -> tuple[str, list[str]]:
    """Pick active replay date and the rotation window of n trading days."""
    days = latest_trading_days(data_root, n, symbol=symbol, exchange=exchange)
    if not days:
        fallback = (explicit or "2021-03-25").strip()[:10]
        return fallback, [fallback]

    explicit_day = (explicit or "").strip()[:10]
    if explicit_day and explicit_day not in days:
        return explicit_day, days if days else [explicit_day]
    if explicit_day and explicit_day in days:
        return explicit_day, days
    return days[-1], days


def week_index_for_date(week_dates: list[str], replay_date: str) -> int:
    day = replay_date.strip()[:10]
    if day in week_dates:
        return week_dates.index(day)
    return max(0, len(week_dates) - 1)
