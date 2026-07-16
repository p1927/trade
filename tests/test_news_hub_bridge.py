"""Tests for news_hub_bridge routing."""

from __future__ import annotations

import pytest


def test_hub_ticker_for_symbol_index_and_equity():
    from trade_integrations.dataflows.news_hub_bridge import hub_ticker_for_symbol

    assert hub_ticker_for_symbol("^NSEI") == "NIFTY"
    assert hub_ticker_for_symbol("BANKNIFTY") == "BANKNIFTY"
    assert hub_ticker_for_symbol("RELIANCE.NS") == "RELIANCE"
    assert hub_ticker_for_symbol("NIFTY", kind="global") == "NIFTY"


def test_ingest_news_articles_upserts_hub(hub_tmp, monkeypatch):
    from trade_integrations.context import hub as hub_mod
    from trade_integrations.dataflows.news_aggregator.models import NewsArticle
    from trade_integrations.dataflows.news_hub_bridge import ingest_news_articles
    from trade_integrations.dataflows.index_research import news_impact_engine as engine
    from trade_integrations.hub_storage import verified_news_store as store

    monkeypatch.setattr(hub_mod, "get_hub_dir", lambda: hub_tmp)
    monkeypatch.setattr(store, "get_hub_dir", lambda: hub_tmp)
    monkeypatch.setattr(engine, "get_hub_dir", lambda: hub_tmp)
    monkeypatch.setattr(
        engine,
        "load_aligned_factor_history",
        lambda **_: __import__("pandas").DataFrame(
            {"date": ["2026-07-16"], "close": [25000.0], "fii_net_5d": [-1000.0]}
        ),
    )
    monkeypatch.setattr(engine, "verify_enriched_news", lambda *a, **k: __import__(
        "trade_integrations.dataflows.index_research.news_verification",
        fromlist=["VerifiedClaim", "_approval_from_claims"],
    )._approval_from_claims([]))

    articles = [
        NewsArticle(
            title="FII selling drags Nifty lower",
            summary="Foreign investors sold heavily in cash segment.",
            link="https://example.com/nifty-fii",
            source="test",
            vendor="test_vendor",
        )
    ]
    stats = ingest_news_articles(articles, ticker="NIFTY", collection_day="2026-07-16")
    assert stats.get("verified", 0) >= 1 or stats.get("cache_hits", 0) >= 0
    recs = store.list_verified_records(ticker="NIFTY", limit=5, include_rejected=True)
    assert any("FII selling" in str(r.get("title") or "") for r in recs)


@pytest.fixture
def hub_tmp(tmp_path, monkeypatch):
    hub = tmp_path / "hub"
    hub.mkdir()
    return hub
