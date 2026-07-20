"""Per-source synchronous queue with min-interval spacing."""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field

from trade_integrations.tiered_api.registry import min_interval


@dataclass
class _SourceQueue:
    lock: threading.Lock = field(default_factory=threading.Lock)
    last_call_at: float = 0.0
    depth: int = 0


_QUEUES: dict[str, _SourceQueue] = {}
_QUEUES_GUARD = threading.Lock()


def _get_queue(source: str) -> _SourceQueue:
    key = source.strip().lower()
    with _QUEUES_GUARD:
        if key not in _QUEUES:
            _QUEUES[key] = _SourceQueue()
        return _QUEUES[key]


def queue_depth(source: str) -> int:
    return _get_queue(source).depth


def acquire_drain_slot(source: str) -> None:
    """Block until caller holds the per-source drain slot and spacing elapsed."""
    q = _get_queue(source)
    q.lock.acquire()
    q.depth += 1
    try:
        spacing = min_interval(source)
        if spacing > 0:
            now = time.monotonic()
            wait = spacing - (now - q.last_call_at)
            if wait > 0:
                time.sleep(wait)
    except Exception:
        q.depth = max(0, q.depth - 1)
        q.lock.release()
        raise


def release_drain_slot(source: str) -> None:
    """Release drain slot and record last call time."""
    q = _get_queue(source)
    q.last_call_at = time.monotonic()
    q.depth = max(0, q.depth - 1)
    if q.lock.locked():
        q.lock.release()


def reset_queues_for_tests() -> None:
    """Clear queue state (tests only)."""
    with _QUEUES_GUARD:
        _QUEUES.clear()
