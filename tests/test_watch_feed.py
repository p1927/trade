"""Tests for WatchFeed port types."""

from __future__ import annotations

import pytest

from trade_integrations.execution.watch_feed import InMemoryWatchFeed, WatchTick


@pytest.mark.unit
def test_in_memory_watch_feed_subscribe_poll_close() -> None:
    feed = InMemoryWatchFeed(context_generation="gen-1")
    feed.subscribe(["NIFTY", "BANKNIFTY"])
    feed.push_tick("NIFTY", 24500.0, ts="2026-07-23T10:00:00+00:00")
    feed.push_tick("BANKNIFTY", 52000.0, ts="2026-07-23T10:00:01+00:00")

    ticks = feed.poll_ticks()

    assert len(ticks) == 2
    assert ticks[0] == WatchTick(
        symbol="NIFTY",
        ltp=24500.0,
        ts="2026-07-23T10:00:00+00:00",
        context_generation="gen-1",
    )
    assert feed.poll_ticks() == []

    feed.close()
    feed.push_tick("NIFTY", 1.0, ts="2026-07-23T10:00:02+00:00")
    assert feed.poll_ticks() == []


@pytest.mark.unit
def test_in_memory_watch_feed_normalizes_symbols() -> None:
    feed = InMemoryWatchFeed(context_generation="gen-2")
    feed.subscribe([" nifty "])
    feed.push_tick(" nifty ", 100.0, ts="t1")

    ticks = feed.poll_ticks()

    assert len(ticks) == 1
    assert ticks[0].symbol == "NIFTY"
