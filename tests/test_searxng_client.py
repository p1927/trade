"""Tests for SearXNG global client (cross-process drain slot)."""

from __future__ import annotations

import multiprocessing
import sys
import threading
import time

import pytest
import requests

from trade_integrations.dataflows import searxng_client


@pytest.fixture(autouse=True)
def _reset_client(monkeypatch, tmp_path):
    monkeypatch.setenv("SEARXNG_MIN_INTERVAL_SEC", "0")
    monkeypatch.setattr("trade_integrations.context.hub.get_hub_dir", lambda: tmp_path)
    searxng_client.reset_searxng_client_for_tests()
    yield
    searxng_client.reset_searxng_client_for_tests()


@pytest.mark.unit
def test_search_json_serializes_concurrent_calls(monkeypatch):
    in_flight = {"n": 0}
    max_in_flight = {"n": 0}
    guard = threading.Lock()
    calls = {"n": 0}

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"results": [{"title": f"Hit {calls['n']}", "url": "https://example.com"}]}

    def fake_get(*args, **kwargs):
        with guard:
            in_flight["n"] += 1
            max_in_flight["n"] = max(max_in_flight["n"], in_flight["n"])
        time.sleep(0.05)
        with guard:
            in_flight["n"] -= 1
        calls["n"] += 1
        return FakeResponse()

    monkeypatch.setattr(searxng_client.requests, "get", fake_get)
    monkeypatch.setattr(searxng_client, "_base_url", lambda: "http://searxng.test")

    threads = [
        threading.Thread(
            target=lambda: searxng_client.search_json("RELIANCE stock news", categories="news"),
        )
        for _ in range(4)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)

    assert max_in_flight["n"] == 1
    assert calls["n"] == 4


@pytest.mark.unit
def test_searxng_news_search_uses_client(monkeypatch):
    from trade_integrations.dataflows import searxng_news

    calls = {"n": 0}

    def fake_search(q, **kwargs):
        calls["n"] += 1
        return {"results": [{"title": "Headline", "url": "https://example.com/a"}]}

    monkeypatch.setattr(searxng_news, "search_json", fake_search)

    results = searxng_news._search("RELIANCE stock news", 5)
    assert calls["n"] == 1
    assert len(results) == 1
    assert results[0]["title"] == "Headline"


@pytest.mark.unit
def test_min_interval_spacing(monkeypatch):
    monkeypatch.setenv("SEARXNG_MIN_INTERVAL_SEC", "0.2")
    sleeps: list[float] = []
    monkeypatch.setattr(searxng_client.time, "sleep", lambda s: sleeps.append(s))
    monkeypatch.setattr(searxng_client.time, "time", lambda: 1000.0)

    searxng_client._write_last_call_epoch(999.9)

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"results": []}

    monkeypatch.setattr(searxng_client.requests, "get", lambda *a, **k: FakeResponse())
    monkeypatch.setattr(searxng_client, "_base_url", lambda: "http://searxng.test")

    searxng_client.search_json("spacing test")
    assert sleeps and sleeps[0] == pytest.approx(0.1, abs=0.02)


def _child_hold_drain(hub_dir: str, hold_seconds: float, ready: multiprocessing.Event) -> None:
    from trade_integrations.context import hub as hub_mod
    from trade_integrations.dataflows import searxng_client as client

    hub_mod.get_hub_dir = lambda: __import__("pathlib").Path(hub_dir)
    ready.set()
    with client._DrainLock():
        time.sleep(hold_seconds)


@pytest.mark.unit
def test_cross_process_drain_lock_blocks(tmp_path):
    if sys.platform == "win32":
        pytest.skip("fcntl flock is unavailable on Windows")

    ready = multiprocessing.Event()
    proc = multiprocessing.Process(
        target=_child_hold_drain,
        args=(str(tmp_path), 0.4, ready),
    )
    proc.start()
    assert ready.wait(timeout=5)

    started = time.monotonic()
    with searxng_client._DrainLock():
        elapsed = time.monotonic() - started
    proc.join(timeout=5)
    assert elapsed >= 0.15
    assert proc.exitcode == 0


@pytest.mark.unit
def test_search_json_propagates_http_errors(monkeypatch):
    def fake_get(*args, **kwargs):
        raise requests.HTTPError("429 Too Many Requests")

    monkeypatch.setattr(searxng_client.requests, "get", fake_get)
    monkeypatch.setattr(searxng_client, "_base_url", lambda: "http://searxng.test")

    with pytest.raises(requests.HTTPError):
        searxng_client.search_json("fail query")
