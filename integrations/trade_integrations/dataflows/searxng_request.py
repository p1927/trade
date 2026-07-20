"""Synchronous in-process queue for SearXNG /search HTTP calls."""

from __future__ import annotations

import os
import threading
import time
from typing import Callable, TypeVar

T = TypeVar("T")

_lock = threading.Lock()
_last_call_at: float = 0.0
_depth: int = 0


def _min_interval() -> float:
    raw = os.environ.get("SEARXNG_MIN_INTERVAL_SEC", "0.5")
    try:
        return max(0.0, float(raw))
    except ValueError:
        return 0.5


def searxng_queue_depth() -> int:
    return _depth


def reset_searxng_queue_for_tests() -> None:
    global _last_call_at, _depth
    with _lock:
        _last_call_at = 0.0
        _depth = 0


def run_searxng_search(fetch_fn: Callable[[], T]) -> T:
    """Acquire drain slot, optionally wait for min interval, run fetch_fn."""
    global _last_call_at, _depth
    _lock.acquire()
    _depth += 1
    try:
        spacing = _min_interval()
        if spacing > 0 and _last_call_at > 0:
            now = time.monotonic()
            wait = spacing - (now - _last_call_at)
            if wait > 0:
                time.sleep(wait)
        return fetch_fn()
    finally:
        _last_call_at = time.monotonic()
        _depth = max(0, _depth - 1)
        if _lock.locked():
            _lock.release()
