"""Rolling poll→evaluate latency metrics for Nautilus watch (p50/p95)."""

from __future__ import annotations

import logging
import statistics
import threading
import time
from collections import deque

logger = logging.getLogger(__name__)

_WARN_P95_SEC = 3.0
_MAX_SAMPLES = 200


class PollLatencyTracker:
    """Thread-safe rolling window of poll-to-evaluate durations."""

    def __init__(self, *, max_samples: int = _MAX_SAMPLES, warn_p95_sec: float = _WARN_P95_SEC) -> None:
        self._max_samples = max(10, max_samples)
        self._warn_p95_sec = warn_p95_sec
        self._samples: deque[float] = deque(maxlen=self._max_samples)
        self._lock = threading.Lock()

    def record(self, duration_sec: float) -> None:
        if duration_sec < 0:
            return
        with self._lock:
            self._samples.append(duration_sec)
            if len(self._samples) < 20:
                return
            ordered = sorted(self._samples)
            p50 = statistics.median(ordered)
            idx95 = min(len(ordered) - 1, int(len(ordered) * 0.95))
            p95 = ordered[idx95]
            if p95 > self._warn_p95_sec:
                logger.warning(
                    "watch poll latency high p50=%.3fs p95=%.3fs n=%d (p95 target < %.1fs)",
                    p50,
                    p95,
                    len(ordered),
                    self._warn_p95_sec,
                )

    def snapshot(self) -> dict[str, float | int]:
        with self._lock:
            if not self._samples:
                return {"count": 0, "p50_sec": 0.0, "p95_sec": 0.0}
            ordered = sorted(self._samples)
            idx95 = min(len(ordered) - 1, int(len(ordered) * 0.95))
            return {
                "count": len(ordered),
                "p50_sec": float(statistics.median(ordered)),
                "p95_sec": float(ordered[idx95]),
            }


_default_tracker = PollLatencyTracker()


def get_poll_latency_tracker() -> PollLatencyTracker:
    return _default_tracker


def record_poll_eval_latency(duration_sec: float) -> None:
    _default_tracker.record(duration_sec)


class poll_eval_timer:
    """Context manager: record monotonic elapsed seconds on exit."""

    def __enter__(self) -> poll_eval_timer:
        self._t0 = time.perf_counter()
        return self

    def __exit__(self, *args: object) -> None:
        record_poll_eval_latency(time.perf_counter() - self._t0)
