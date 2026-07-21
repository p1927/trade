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
