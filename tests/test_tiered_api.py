"""Tests for tiered_api request keys, hub cache, budget, and queue."""

from __future__ import annotations

import json
import threading
from pathlib import Path

import pytest

from trade_integrations.tiered_api import budget, hub_store, queue
from trade_integrations.tiered_api.client import tiered_fetch
from trade_integrations.tiered_api.request_key import TieredRequest, request_hash


@pytest.fixture
def hub_tmp(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("TRADE_STACK_HUB_DIR", str(tmp_path))
    monkeypatch.setenv("TIERED_API_ENABLED", "1")
    monkeypatch.setenv("TIERED_API_ALPHA_VANTAGE_DAILY_LIMIT", "2")
    monkeypatch.setenv("TIERED_API_ALPHA_VANTAGE_MIN_INTERVAL", "0")
    monkeypatch.setenv("TIERED_API_ALPHA_VANTAGE_HUB_TTL_HOURS", "24")
    monkeypatch.setenv("ALPHA_VANTAGE_API_KEY", "test-key")
    queue.reset_queues_for_tests()
    yield tmp_path
    queue.reset_queues_for_tests()


@pytest.mark.unit
def test_request_hash_strips_apikey():
    req_a = TieredRequest(url="https://example.com/q", params={"symbol": "AAPL", "apikey": "secret1"})
    req_b = TieredRequest(url="https://example.com/q", params={"symbol": "AAPL", "apikey": "secret2"})
    assert request_hash("alpha_vantage", req_a) == request_hash("alpha_vantage", req_b)


@pytest.mark.unit
def test_tiered_fetch_cache_hit_skips_budget(hub_tmp, monkeypatch):
    monkeypatch.setenv("TIERED_API_RAW_CACHE", "1")
    req = TieredRequest(url="https://example.com/data", params={"symbol": "TEST"})
    calls = {"n": 0}

    def fetch_fn():
        calls["n"] += 1
        return {"value": 42}

    r1 = tiered_fetch("alpha_vantage", req, fetch_fn, skip_policy_check=True)
    assert r1.cache_hit is False
    assert calls["n"] == 1
    assert r1.budget["calls"] == 1

    r2 = tiered_fetch("alpha_vantage", req, fetch_fn, skip_policy_check=True)
    assert r2.cache_hit is True
    assert calls["n"] == 1
    assert r2.budget["calls"] == 1


@pytest.mark.unit
def test_budget_exhausted_serves_stale_cache(hub_tmp, monkeypatch):
    monkeypatch.setenv("TIERED_API_RAW_CACHE", "1")
    monkeypatch.setenv("TIERED_API_ALPHA_VANTAGE_HUB_TTL_HOURS", "1")
    req = TieredRequest(url="https://example.com/stale", params={"symbol": "STALE"})
    req_hash = request_hash("alpha_vantage", req)
    hub_store.save_cached("alpha_vantage", req_hash, {"value": "cached"}, request_meta={})

    path = hub_store._cache_path("alpha_vantage", req_hash)
    envelope = json.loads(path.read_text(encoding="utf-8"))
    envelope["fetched_at"] = "2020-01-01T00:00:00+00:00"
    path.write_text(json.dumps(envelope), encoding="utf-8")

    for _ in range(2):
        tiered_fetch(
            "alpha_vantage",
            TieredRequest(url="https://example.com/other", params={"i": _}),
            lambda: {"fresh": True},
            skip_policy_check=True,
        )

    def should_not_run():
        raise AssertionError("fetch_fn should not run when stale cache is served")

    result = tiered_fetch("alpha_vantage", req, should_not_run, skip_policy_check=True)
    assert result.cache_hit is True
    assert result.data == {"value": "cached"}


@pytest.mark.unit
def test_budget_exhausted(hub_tmp):
    req = TieredRequest(url="https://example.com/x", params={"i": 1})
    counter = {"n": 0}

    def fetch_fn():
        counter["n"] += 1
        return counter["n"]

    tiered_fetch("alpha_vantage", req, fetch_fn, skip_policy_check=True)
    req2 = TieredRequest(url="https://example.com/x", params={"i": 2})
    tiered_fetch("alpha_vantage", req2, fetch_fn, skip_policy_check=True)

    from trade_integrations.tiered_api.errors import TieredApiBudgetExhausted

    req3 = TieredRequest(url="https://example.com/x", params={"i": 3})
    with pytest.raises(TieredApiBudgetExhausted):
        tiered_fetch("alpha_vantage", req3, fetch_fn, skip_policy_check=True)


@pytest.mark.unit
def test_hub_store_ttl(hub_tmp):
    req_hash = "abc123"
    hub_store.save_cached("alpha_vantage", req_hash, {"x": 1}, request_meta={})
    cached = hub_store.load_cached("alpha_vantage", req_hash)
    assert cached is not None
    assert cached["data"] == {"x": 1}


@pytest.mark.unit
def test_queue_serializes_concurrent(hub_tmp, monkeypatch):
    monkeypatch.setenv("TIERED_API_ALPHA_VANTAGE_DAILY_LIMIT", "10")
    in_flight = {"n": 0}
    max_in_flight = {"n": 0}
    guard = threading.Lock()

    def work(i: int):
        req = TieredRequest(url="https://example.com/q", params={"n": i})

        def fetch_fn():
            with guard:
                in_flight["n"] += 1
                max_in_flight["n"] = max(max_in_flight["n"], in_flight["n"])
            threading.Event().wait(0.05)
            with guard:
                in_flight["n"] -= 1
            return i

        tiered_fetch("alpha_vantage", req, fetch_fn, skip_policy_check=True)

    threads = [threading.Thread(target=work, args=(i,)) for i in range(3)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)
    assert max_in_flight["n"] == 1
