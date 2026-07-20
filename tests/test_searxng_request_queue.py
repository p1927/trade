"""Tests for SearXNG synchronous request queue."""

from __future__ import annotations

import threading

import pytest

from trade_integrations.dataflows import searxng_request


@pytest.fixture(autouse=True)
def _reset_queue(monkeypatch):
    monkeypatch.setenv("SEARXNG_MIN_INTERVAL_SEC", "0")
    searxng_request.reset_searxng_queue_for_tests()
    yield
    searxng_request.reset_searxng_queue_for_tests()


@pytest.mark.unit
def test_run_searxng_search_serializes_concurrent_calls():
    in_flight = {"n": 0}
    max_in_flight = {"n": 0}
    guard = threading.Lock()

    def fetch_fn():
        with guard:
            in_flight["n"] += 1
            max_in_flight["n"] = max(max_in_flight["n"], in_flight["n"])
        threading.Event().wait(0.05)
        with guard:
            in_flight["n"] -= 1
        return in_flight["n"]

    threads = [
        threading.Thread(
            target=lambda: searxng_request.run_searxng_search(fetch_fn),
        )
        for _ in range(4)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)

    assert max_in_flight["n"] == 1


@pytest.mark.unit
def test_searxng_news_search_uses_queue(monkeypatch):
    from trade_integrations.dataflows import searxng_news

    calls = {"n": 0}

    def fake_run(fetch_fn):
        calls["n"] += 1
        return fetch_fn()

    monkeypatch.setattr(searxng_news, "run_searxng_search", fake_run)

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"results": [{"title": "Headline", "url": "https://example.com/a"}]}

    monkeypatch.setattr(searxng_news.requests, "get", lambda *a, **k: FakeResponse())
    monkeypatch.setattr(searxng_news, "_base_url", lambda: "http://searxng.test")

    results = searxng_news._search("RELIANCE stock news", 5)
    assert calls["n"] == 1
    assert len(results) == 1
    assert results[0]["title"] == "Headline"
