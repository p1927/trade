"""Tests for SearXNG finance enrichment helpers."""

from __future__ import annotations


def test_parse_scalar_from_results_finds_nifty_pe():
    from trade_integrations.dataflows.searxng_finance import parse_scalar_from_results

    results = [
        {
            "title": "Nifty 50 PE ratio at 23.4 as of July 2026",
            "content": "Index valuation remains elevated",
            "url": "https://www.moneycontrol.com/news/business/markets/",
        }
    ]
    assert parse_scalar_from_results(results) == 23.4


def test_fetch_nifty_trailing_pe_via_searxng(monkeypatch):
    from trade_integrations.dataflows import searxng_finance

    monkeypatch.setattr(
        searxng_finance,
        "search_finance",
        lambda query, **kwargs: [
            {
                "title": "Nifty trailing P/E 21.8",
                "content": "",
                "url": "https://www.screener.in/market/",
                "engines": ["screener india"],
            }
        ],
    )

    payload = searxng_finance.fetch_nifty_trailing_pe_via_searxng()
    assert payload is not None
    assert payload["value"] == 21.8
    assert payload["source"] == "searxng_finance"


def test_fetch_rbi_macro_via_searxng(monkeypatch):
    from trade_integrations.dataflows import searxng_finance

    def _search(query, **kwargs):
        if "repo" in query.lower():
            return [
                {
                    "title": "RBI keeps repo rate unchanged at 6.5 per cent",
                    "content": "",
                    "url": "https://www.moneycontrol.com/news/economy/policy/",
                }
            ]
        return [
            {
                "title": "India CPI inflation at 4.2% in June",
                "content": "",
                "url": "https://www.livemint.com/economy/",
            }
        ]

    monkeypatch.setattr(searxng_finance, "search_finance", _search)

    payload = searxng_finance.fetch_rbi_macro_via_searxng()
    assert payload["repo_rate"] == 6.5
    assert payload["cpi_yoy_proxy"] == 4.2
    assert payload["source"] == "searxng_finance"


def test_search_finance_retries_transient_unresponsive(monkeypatch):
    from trade_integrations.dataflows import searxng_finance

    calls = {"n": 0}

    def fake_search_json(query, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            return {
                "results": [],
                "unresponsive_engines": [["bing", "timeout"]],
            }
        return {
            "results": [
                {
                    "title": "RBI keeps repo rate unchanged at 6.5 per cent",
                    "content": "",
                    "url": "https://www.moneycontrol.com/news/economy/policy/",
                }
            ],
            "unresponsive_engines": [],
        }

    monkeypatch.setattr(searxng_finance, "search_json", fake_search_json)
    monkeypatch.setattr(searxng_finance, "searxng_finance_engines", lambda: "bing")
    monkeypatch.setattr(searxng_finance.time, "sleep", lambda _s: None)

    rows = searxng_finance.search_finance("RBI repo rate India", limit=1)
    assert calls["n"] == 2
    assert len(rows) == 1


def test_search_finance_skips_hard_unresponsive_engine(monkeypatch):
    from trade_integrations.dataflows import searxng_finance

    calls: list[str | None] = []

    def fake_search_json(query, **kwargs):
        engine = kwargs.get("engines")
        calls.append(engine)
        if engine == "bing":
            return {
                "results": [],
                "unresponsive_engines": [["bing", "CAPTCHA"]],
            }
        return {
            "results": [
                {
                    "title": "Nifty 50 PE ratio at 22.1",
                    "content": "",
                    "url": "https://www.moneycontrol.com/news/business/markets/",
                }
            ],
            "unresponsive_engines": [],
        }

    monkeypatch.setattr(searxng_finance, "search_json", fake_search_json)
    monkeypatch.setattr(searxng_finance, "searxng_finance_engines", lambda: "bing,backup")
    monkeypatch.setattr(searxng_finance.time, "sleep", lambda _s: None)

    rows = searxng_finance.search_finance("Nifty 50 PE ratio", limit=1)
    assert calls[:2] == ["bing", "backup"]
    assert len(rows) == 1


def test_search_finance_allowed_domains_accepts_broker_urls(monkeypatch):
    from trade_integrations.dataflows import searxng_finance

    broker_row = {
        "title": "Motilal Oswal Nifty 50 weekly forecast",
        "content": "Target 26500",
        "url": "https://www.motilaloswal.com/research/nifty-outlook",
    }

    monkeypatch.setattr(
        searxng_finance,
        "search_json",
        lambda query, **kwargs: {"results": [broker_row], "unresponsive_engines": []},
    )
    monkeypatch.setattr(searxng_finance, "searxng_finance_engines", lambda: "bing")

    default_rows = searxng_finance.search_finance("Motilal Oswal Nifty forecast", limit=1)
    assert default_rows == []

    allowed_rows = searxng_finance.search_finance(
        "Motilal Oswal Nifty forecast",
        limit=1,
        allowed_domains=("motilaloswal.com",),
    )
    assert len(allowed_rows) == 1
    assert "motilaloswal.com" in allowed_rows[0]["url"]


def test_search_finance_stats_reports_raw_count_before_filter(monkeypatch):
    from trade_integrations.dataflows import searxng_finance

    rows_payload = [
        {
            "title": "Broker outlook",
            "content": "",
            "url": "https://www.motilaloswal.com/research/nifty",
        },
        {
            "title": "MC markets",
            "content": "",
            "url": "https://www.moneycontrol.com/news/business/markets/",
        },
    ]

    monkeypatch.setattr(
        searxng_finance,
        "search_json",
        lambda query, **kwargs: {"results": rows_payload, "unresponsive_engines": []},
    )
    monkeypatch.setattr(searxng_finance, "searxng_finance_engines", lambda: "bing")

    stats: dict[str, int] = {}
    filtered = searxng_finance.search_finance(
        "Nifty forecast",
        limit=8,
        allowed_domains=("motilaloswal.com",),
        stats=stats,
    )
    assert len(filtered) == 1
    assert stats.get("raw_count") == 2


def test_search_finance_empty_allowed_domains_rejects_all(monkeypatch):
    from trade_integrations.dataflows import searxng_finance

    row = {
        "title": "Broker outlook",
        "content": "",
        "url": "https://www.motilaloswal.com/research/nifty",
    }
    monkeypatch.setattr(
        searxng_finance,
        "search_json",
        lambda query, **kwargs: {"results": [row], "unresponsive_engines": []},
    )
    monkeypatch.setattr(searxng_finance, "searxng_finance_engines", lambda: "bing")

    stats: dict[str, int] = {}
    filtered = searxng_finance.search_finance(
        "Nifty forecast",
        limit=1,
        allowed_domains=(),
        stats=stats,
    )
    assert filtered == []
    assert stats.get("raw_count") == 1


def test_search_source_outcome_skips_when_no_domains(monkeypatch) -> None:
    from trade_integrations.dataflows.index_research.external_predictions.fetcher import (
        search_source_results_with_outcome,
    )
    from trade_integrations.dataflows.index_research.external_predictions.models import (
        ExternalPredictionSource,
    )
    from trade_integrations.dataflows.index_research.pipeline_log import PipelineLogger

    source = ExternalPredictionSource(
        id="broken",
        display_name="Broken Source",
        kind="media",
        domains=[],
        search_queries=['"{source_name}" Nifty 50 target {today}'],
    )

    def fail_search(*args, **kwargs):
        raise AssertionError("search_finance should not run when domains empty")

    monkeypatch.setattr(
        "trade_integrations.dataflows.index_research.external_predictions.fetcher.search_finance",
        fail_search,
    )

    pl = PipelineLogger()
    outcome = search_source_results_with_outcome(source, horizon_days=14, pipeline=pl)
    assert outcome.queries_run == 0
    assert outcome.hits == []


def test_search_source_outcome_tracks_domain_filter_exhausted(monkeypatch) -> None:
    from trade_integrations.dataflows.index_research.external_predictions.fetcher import (
        search_source_results_with_outcome,
    )
    from trade_integrations.dataflows.index_research.external_predictions.models import (
        ExternalPredictionSource,
    )
    from trade_integrations.dataflows.index_research.pipeline_log import PipelineLogger

    source = ExternalPredictionSource(
        id="motilal_oswal",
        display_name="Motilal Oswal",
        kind="broker",
        domains=["motilaloswal.com"],
        search_queries=['"{source_name}" Nifty 50 target {today}'],
    )

    def fake_search_finance(query, **kwargs):
        stats = kwargs.get("stats")
        if stats is not None:
            stats["raw_count"] = 3
        return []

    monkeypatch.setattr(
        "trade_integrations.dataflows.index_research.external_predictions.fetcher.search_finance",
        fake_search_finance,
    )

    pl = PipelineLogger()
    outcome = search_source_results_with_outcome(source, horizon_days=14, pipeline=pl)
    assert outcome.domain_filter_exhausted is True
    assert any("after domain filter" in getattr(e, "message", "") for e in pl.entries)


def test_resolve_nifty_trailing_pe_prefers_yfinance(monkeypatch):
    from trade_integrations.dataflows.index_research.sources import nifty_pe_fetch

    monkeypatch.setattr(
        nifty_pe_fetch,
        "_fetch_yfinance_index_pe",
        lambda: {"value": 24.5, "source": "yfinance", "metadata": {}},
    )

    def _fail_weighted():
        raise AssertionError("should not run")

    monkeypatch.setattr(nifty_pe_fetch, "_fetch_weighted_constituent_pe", _fail_weighted)

    out = nifty_pe_fetch.resolve_nifty_trailing_pe()
    assert out is not None
    assert out["value"] == 24.5
    assert out["source"] == "yfinance"
