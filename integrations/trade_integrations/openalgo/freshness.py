"""Freshness policies and in-process L1 dedupe for hub channel reads."""

from __future__ import annotations

import os
import threading
import time
from enum import Enum
from typing import Any


class FreshnessPolicy(str, Enum):
    LIVE = "live"
    NORMAL = "normal"
    WATCH = "watch"


def _options_cache_ttl_minutes() -> int:
    try:
        return max(0, int(os.getenv("TRADINGAGENTS_OPTIONS_CACHE_MINUTES", "30")))
    except ValueError:
        return 30


def _watch_quote_ttl_seconds() -> int:
    try:
        return max(0, int(os.getenv("OPENALGO_WATCH_QUOTE_TTL_SECONDS", "5")))
    except ValueError:
        return 5


def ttl_seconds(policy: FreshnessPolicy) -> int:
    """Return cache TTL in seconds for the given freshness policy."""
    if policy == FreshnessPolicy.LIVE:
        return 0
    if policy == FreshnessPolicy.WATCH:
        return _watch_quote_ttl_seconds()
    return _options_cache_ttl_minutes() * 60


class L1Cache:
    """Thread-safe in-process cache keyed by symbol/series with monotonic expiry."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._entries: dict[str, tuple[Any, float]] = {}

    def get(self, key: str) -> Any | None:
        with self._lock:
            entry = self._entries.get(key)
            if entry is None:
                return None
            value, expires_at = entry
            if time.monotonic() >= expires_at:
                del self._entries[key]
                return None
            return value

    def set(self, key: str, value: Any, *, ttl_seconds: int) -> None:
        if ttl_seconds <= 0:
            return
        with self._lock:
            self._entries[key] = (value, time.monotonic() + ttl_seconds)
