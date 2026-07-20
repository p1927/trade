"""Tests for trading-day cache used by index research."""

from __future__ import annotations

import json

import pytest


@pytest.mark.unit
def test_get_or_fetch_miss_then_hit(tmp_path, monkeypatch):
    hub = tmp_path / "hub"
    hub.mkdir()
    monkeypatch.setenv("TRADE_STACK_HUB_DIR", str(hub))

    from trade_integrations.dataflows.index_research import day_cache

    calls = {"n": 0}

    def fetch() -> dict:
        calls["n"] += 1
        return {"value": 42}

    payload1, cached1 = day_cache.get_or_fetch(
        namespace="flow_coverage",
        trading_day="2026-07-20",
        fetch_fn=fetch,
    )
    assert cached1 is False
    assert payload1 == {"value": 42}
    assert calls["n"] == 1

    payload2, cached2 = day_cache.get_or_fetch(
        namespace="flow_coverage",
        trading_day="2026-07-20",
        fetch_fn=fetch,
    )
    assert cached2 is True
    assert payload2 == {"value": 42}
    assert calls["n"] == 1

    path = day_cache.cache_path(namespace="flow_coverage", trading_day="2026-07-20")
    assert path.is_file()
    envelope = json.loads(path.read_text(encoding="utf-8"))
    assert envelope["trading_day"] == "2026-07-20"
    assert envelope["payload"] == {"value": 42}


@pytest.mark.unit
def test_get_or_fetch_force_refresh(tmp_path, monkeypatch):
    hub = tmp_path / "hub"
    hub.mkdir()
    monkeypatch.setenv("TRADE_STACK_HUB_DIR", str(hub))

    from trade_integrations.dataflows.index_research import day_cache

    day_cache.write_cached(
        namespace="macro_snapshot",
        trading_day="2026-07-20",
        payload={"old": True},
    )

    payload, cached = day_cache.get_or_fetch(
        namespace="macro_snapshot",
        trading_day="2026-07-20",
        fetch_fn=lambda: {"old": False},
        force=True,
    )
    assert cached is False
    assert payload == {"old": False}


@pytest.mark.unit
def test_invalidate_removes_entry(tmp_path, monkeypatch):
    hub = tmp_path / "hub"
    hub.mkdir()
    monkeypatch.setenv("TRADE_STACK_HUB_DIR", str(hub))

    from trade_integrations.dataflows.index_research import day_cache

    day_cache.write_cached(namespace="nifty_pe", trading_day="2026-07-20", payload=22.5)
    path = day_cache.cache_path(namespace="nifty_pe", trading_day="2026-07-20")
    assert path.is_file()
    assert day_cache.invalidate(namespace="nifty_pe", trading_day="2026-07-20") is True
    assert not path.is_file()
    assert day_cache.read_cached(namespace="nifty_pe", trading_day="2026-07-20") is None
