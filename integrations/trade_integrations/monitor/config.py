"""Configuration for the opt-in options plan monitor."""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class MonitorConfig:
    enabled: bool = False
    spot_drift_pct: float = 1.5
    max_age_minutes: int = 30
    poll_cron: str = "*/5 * * * *"
    watchlist: tuple[str, ...] = ("NIFTY", "BANKNIFTY")


def _env_bool(name: str, default: str = "false") -> bool:
    raw = os.getenv(name, default).strip().lower()
    return raw not in {"0", "false", "no", "off"}


def is_monitor_enabled() -> bool:
    """Return True when the realtime options monitor master switch is on."""
    return _env_bool("OPTIONS_REALTIME_MONITOR_ENABLED", "false")


def get_monitor_config() -> MonitorConfig:
    """Load monitor thresholds and watchlist from environment."""
    watchlist_raw = os.getenv("OPTIONS_MONITOR_WATCHLIST", "NIFTY,BANKNIFTY")
    watchlist = tuple(
        item.strip().upper()
        for item in watchlist_raw.split(",")
        if item.strip()
    )
    return MonitorConfig(
        enabled=is_monitor_enabled(),
        spot_drift_pct=float(os.getenv("OPTIONS_MONITOR_SPOT_DRIFT_PCT", "1.5")),
        max_age_minutes=int(os.getenv("OPTIONS_MONITOR_MAX_AGE_MINUTES", "30")),
        poll_cron=os.getenv("OPTIONS_MONITOR_POLL_CRON", "*/5 * * * *"),
        watchlist=watchlist or ("NIFTY", "BANKNIFTY"),
    )
