"""Mocked tests for progressive external-predictions search agent."""

from __future__ import annotations

from pathlib import Path

import pytest

from trade_integrations.dataflows.crawl4ai_client import CrawlPageResult
from trade_integrations.dataflows.index_research.external_predictions.models import (
    ExternalPredictionRecord,
    ExternalPredictionSource,
    ExternalPredictionTarget,
)
from trade_integrations.dataflows.index_research.external_predictions.search_agent import (
    finance_engine_chain,
    passes_verified_quality_gates,
    progressive_search_until_forecast,
)


@pytest.fixture
def hub_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    hub = tmp_path / "hub"
    hub.mkdir()
    monkeypatch.setenv("TRADE_STACK_HUB_DIR", str(hub))
    return hub


@pytest.fixture(autouse=True)
def _reset_crawl4ai_after_test() -> None:
    yield
    from trade_integrations.dataflows.crawl4ai_client import reset_crawl4ai_client_for_tests

    reset_crawl4ai_client_for_tests()


def _source() -> ExternalPredictionSource:
    return ExternalPredictionSource(
        id="goldman_sachs",
        display_name="Goldman Sachs",
        domains=["economictimes.indiatimes.com"],
        kind="global_bank",
        search_queries=['"{display_name}" Nifty 50 target'],
    )


def test_finance_engine_chain_fallback_a_fails_b_hits(monkeypatch: pytest.MonkeyPatch) -> None:
    from trade_integrations.dataflows.index_research.external_predictions import search_agent as sa

    monkeypatch.setattr(sa, "finance_engine_chain", lambda: ["duckduckgo", "bing"])
    calls: list[str] = []

    def _search_one(query, *, engine, **kwargs):
        calls.append(engine)
        if engine == "duckduckgo":
            return [], True, 0
        return [
            {
                "url": "https://economictimes.indiatimes.com/markets/indices/nifty-50",
                "title": "Goldman Sachs Nifty 50 target 26500",
                "content": "Goldman Sachs raises Nifty 50 target",
            }
        ], False, 1

    monkeypatch.setattr(sa, "search_finance_one", _search_one)

    ok = ExternalPredictionRecord(
        source_id="goldman_sachs",
        symbol="NIFTY",
        horizon_days=14,
        fetch_status="ok",
        confidence="high",
        target=ExternalPredictionTarget(mid=26500.0),
        extraction={"vision_checked": True},
    )

    monkeypatch.setattr(
        sa,
        "crawl_single_url",
        lambda url, **kwargs: (
            url,
            CrawlPageResult(url=url, success=True, markdown="Goldman Sachs Nifty 50 target 26500"),
        ),
    )
    monkeypatch.setattr(sa, "_try_search_candidate", lambda *a, **k: ok)

    outcome = progressive_search_until_forecast(_source(), symbol="NIFTY", horizon_days=14)
    assert "duckduckgo" in calls
    assert "bing" in calls
    assert outcome.record is not None
    assert outcome.record.fetch_status == "ok"


def test_url_dedup_same_url_once(monkeypatch: pytest.MonkeyPatch) -> None:
    from trade_integrations.dataflows.index_research.external_predictions import search_agent as sa

    url = "https://economictimes.indiatimes.com/markets/stocks/news/nifty/articleshow/1.cms"
    hit = {"url": url, "title": "Goldman Sachs Nifty target", "content": "forecast"}

    monkeypatch.setattr(sa, "finance_engine_chain", lambda: ["duckduckgo", "bing"])

    def _search_one(query, *, engine, **kwargs):
        return [dict(hit)], False, 1

    monkeypatch.setattr(sa, "search_finance_one", _search_one)
    try_calls: list[str] = []

    def _try(source, trial, **kwargs):
        try_calls.append(trial.url)
        return ExternalPredictionRecord(
            source_id="goldman_sachs",
            symbol="NIFTY",
            horizon_days=14,
            fetch_status="ok",
            confidence="high",
            target=ExternalPredictionTarget(mid=26500.0),
            extraction={"vision_checked": True},
        )

    monkeypatch.setattr(sa, "_try_search_candidate", _try)

    progressive_search_until_forecast(_source(), symbol="NIFTY", horizon_days=14)
    assert try_calls.count(url) == 1


def test_weak_attribution_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    from trade_integrations.dataflows.index_research.external_predictions import search_agent as sa

    url = "https://economictimes.indiatimes.com/markets/stocks/news/nifty/articleshow/2.cms"
    monkeypatch.setattr(sa, "finance_engine_chain", lambda: ["bing"])
    monkeypatch.setattr(
        sa,
        "search_finance_one",
        lambda *a, **k: (
            [{"url": url, "title": "Nifty outlook", "content": "generic forecast"}],
            False,
            1,
        ),
    )
    monkeypatch.setattr(
        sa,
        "crawl_single_url",
        lambda u, **kwargs: (
            u,
            CrawlPageResult(url=u, success=True, markdown="Nifty 50 target 26500"),
        ),
    )

    weak_ok = ExternalPredictionRecord(
        source_id="goldman_sachs",
        symbol="NIFTY",
        horizon_days=14,
        fetch_status="ok",
        confidence="high",
        target=ExternalPredictionTarget(mid=26500.0),
        extraction={"vision_checked": True},
    )
    monkeypatch.setattr(sa, "_try_search_candidate", lambda *a, **k: weak_ok)

    outcome = progressive_search_until_forecast(_source(), symbol="NIFTY", horizon_days=14)
    assert outcome.record is None or outcome.record.fetch_status != "ok"


def test_low_confidence_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    from trade_integrations.dataflows.index_research.external_predictions import search_agent as sa

    url = "https://economictimes.indiatimes.com/topic/goldman-sachs-nifty"
    monkeypatch.setattr(sa, "finance_engine_chain", lambda: ["bing"])
    monkeypatch.setattr(
        sa,
        "search_finance_one",
        lambda *a, **k: (
            [
                {
                    "url": url,
                    "title": "Goldman Sachs Nifty 50 target",
                    "content": "Goldman Sachs",
                }
            ],
            False,
            1,
        ),
    )
    monkeypatch.setattr(
        sa,
        "crawl_single_url",
        lambda u, **kwargs: (
            u,
            CrawlPageResult(url=u, success=True, markdown="Goldman Sachs Nifty 50 target 26500"),
        ),
    )
    low = ExternalPredictionRecord(
        source_id="goldman_sachs",
        symbol="NIFTY",
        horizon_days=14,
        fetch_status="ok",
        confidence="low",
        target=ExternalPredictionTarget(mid=26500.0),
        extraction={"vision_checked": True},
    )
    monkeypatch.setattr(sa, "_try_search_candidate", lambda *a, **k: low)

    outcome = progressive_search_until_forecast(_source(), symbol="NIFTY", horizon_days=14)
    assert outcome.record is None or outcome.record.fetch_status != "ok"


def test_stale_tries_next(monkeypatch: pytest.MonkeyPatch) -> None:
    from trade_integrations.dataflows.index_research.external_predictions import search_agent as sa

    urls = [
        "https://economictimes.indiatimes.com/markets/a",
        "https://economictimes.indiatimes.com/markets/b",
    ]
    monkeypatch.setattr(sa, "finance_engine_chain", lambda: ["bing"])

    def _search_one(query, *, engine, **kwargs):
        return [
            {"url": u, "title": "Goldman Sachs Nifty target", "content": "Goldman Sachs"}
            for u in urls
        ], False, 2

    monkeypatch.setattr(sa, "search_finance_one", _search_one)
    monkeypatch.setattr(
        sa,
        "crawl_single_url",
        lambda u, **kwargs: (
            u,
            CrawlPageResult(url=u, success=True, markdown="Goldman Sachs Nifty 50 target 26500"),
        ),
    )
    calls = {"n": 0}

    def _try(*_a, **_k):
        calls["n"] += 1
        if calls["n"] == 1:
            return ExternalPredictionRecord(
                source_id="goldman_sachs",
                symbol="NIFTY",
                horizon_days=14,
                fetch_status="stale",
            )
        return ExternalPredictionRecord(
            source_id="goldman_sachs",
            symbol="NIFTY",
            horizon_days=14,
            fetch_status="ok",
            confidence="high",
            target=ExternalPredictionTarget(mid=26500.0),
            extraction={"vision_checked": True},
        )

    monkeypatch.setattr(sa, "_try_search_candidate", _try)
    outcome = progressive_search_until_forecast(_source(), symbol="NIFTY", horizon_days=14)
    assert calls["n"] >= 2
    assert outcome.record is not None
    assert outcome.record.fetch_status == "ok"


def test_passes_verified_quality_gates_syndication() -> None:
    src = _source()
    record = ExternalPredictionRecord(
        source_id=src.id,
        fetch_status="ok",
        confidence="high",
        extraction={"vision_checked": True},
    )
    assert not passes_verified_quality_gates(
        record,
        src,
        "https://economictimes.indiatimes.com/markets/nifty",
        title="Nifty outlook",
        content="no bank name",
    )
    assert passes_verified_quality_gates(
        record,
        src,
        "https://economictimes.indiatimes.com/topic/goldman-sachs-nifty",
        title="Goldman Sachs Nifty 50 target",
        content="Goldman Sachs",
    )


def test_finance_engine_chain_default() -> None:
    engines = finance_engine_chain()
    assert isinstance(engines, list)
    assert len(engines) >= 1
