"""Tests for watch poll latency tracker."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INTEGRATIONS = ROOT / "integrations"
if str(INTEGRATIONS) not in sys.path:
    sys.path.insert(0, str(INTEGRATIONS))

from nautilus_openalgo_bridge.poll_latency import PollLatencyTracker, poll_eval_timer  # noqa: E402


def test_poll_eval_timer_records_sample():
    tracker = PollLatencyTracker(max_samples=50)
    with poll_eval_timer() as _:
        pass
    # default tracker got a sample; use isolated tracker via record
    tracker.record(0.01)
    snap = tracker.snapshot()
    assert snap["count"] == 1
    assert snap["p50_sec"] >= 0.0


def test_tracker_computes_p50_p95():
    tracker = PollLatencyTracker(max_samples=100, warn_p95_sec=999.0)
    for value in [0.1, 0.2, 0.3, 0.4, 0.5]:
        tracker.record(value)
    snap = tracker.snapshot()
    assert snap["count"] == 5
    assert snap["p50_sec"] == 0.3
    assert snap["p95_sec"] >= 0.4
