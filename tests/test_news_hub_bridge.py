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
    from trade_integrations.hub_storage import news_staging_store as staging_store
    from trade_integrations.hub_storage import news_events_store as events_store

    monkeypatch.setattr(hub_mod, "get_hub_dir", lambda: hub_tmp)
    monkeypatch.setattr(store, "get_hub_dir", lambda: hub_tmp)
    monkeypatch.setattr(staging_store, "get_hub_dir", lambda: hub_tmp)
    monkeypatch.setattr(events_store, "get_hub_dir", lambda: hub_tmp)
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
    from trade_integrations.dataflows.index_research import news_entity_worker as worker

    monkeypatch.setattr(worker, "schedule_staging_processing", lambda **k: None)

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
    assert stats.get("queued", 0) >= 1 or stats.get("verified", 0) >= 1
    from trade_integrations.hub_storage.news_staging_store import list_pending_refs

    pending = list_pending_refs(ticker="NIFTY", limit=20)
    recs = store.list_verified_records(ticker="NIFTY", limit=5, include_rejected=True)
    assert any("FII selling" in str(r.get("title") or "") for r in pending) or any(
        "FII selling" in str(r.get("title") or "") for r in recs
    )


def test_rss_entries_use_per_article_urls():
    from trade_integrations.dataflows.index_research.news_dedup import canonical_story_id, merge_raw_headlines
    from trade_integrations.dataflows.news_hub_bridge._ingest import rss_entry_to_hub_row

    feed_url = "https://feeds.example.com/market.rss"
    rows = [
        rss_entry_to_hub_row(
            {"title": "Story A", "summary": "A body", "date": "2026-07-16", "url": "https://news.example.com/a"},
            label="example",
            feed_url=feed_url,
        ),
        rss_entry_to_hub_row(
            {"title": "Story B", "summary": "B body", "date": "2026-07-16", "url": "https://news.example.com/b"},
            label="example",
            feed_url=feed_url,
        ),
    ]
    assert canonical_story_id(rows[0]["title"], rows[0]["url"]) != canonical_story_id(
        rows[1]["title"], rows[1]["url"]
    )
    merged = merge_raw_headlines(rows, ticker="NIFTY")
    assert len(merged) == 2


def test_ingest_reports_pipeline_paused_without_minimax(hub_tmp, monkeypatch):
    from trade_integrations.context import hub as hub_mod
    from trade_integrations.dataflows.news_hub_bridge._ingest import ingest_rows_to_hub
    from trade_integrations.hub_storage import news_staging_store as staging_store

    monkeypatch.setattr(hub_mod, "get_hub_dir", lambda: hub_tmp)
    monkeypatch.setattr(staging_store, "get_hub_dir", lambda: hub_tmp)
    monkeypatch.setattr(staging_store, "is_entity_pipeline_enabled", lambda: True)
    monkeypatch.setattr(staging_store, "minimax_configured", lambda: False)
    monkeypatch.setattr(staging_store, "rule_fallback_distillation_enabled", lambda: False)

    stats = ingest_rows_to_hub(
        [
            {
                "title": "Test headline for paused pipeline",
                "summary": "Body",
                "url": "https://example.com/paused-test",
                "source": "test",
                "published_at": "2026-07-16",
            },
            {
                "title": "Duplicate paused headline",
                "summary": "Body",
                "url": "https://example.com/paused-test",
                "source": "test",
                "published_at": "2026-07-16",
            },
        ],
        ticker="NIFTY",
    )
    assert stats.get("pipeline_paused") is True
    assert stats.get("queued", 0) == 1
    assert stats.get("ingested", 0) == 1
    assert "MINIMAX" in str(stats.get("pause_reason") or "")


def test_query_verified_news_reads_events_ssot(hub_tmp, monkeypatch):
    from trade_integrations.context import hub as hub_mod
    from trade_integrations.dataflows.news_hub_bridge import query_verified_news
    from trade_integrations.hub_storage import news_events_store as events_store
    from trade_integrations.hub_storage import news_staging_store as staging_store
    from trade_integrations.hub_storage.news_event_models import DistilledNewsEvent

    monkeypatch.setattr(hub_mod, "get_hub_dir", lambda: hub_tmp)
    monkeypatch.setattr(events_store, "get_hub_dir", lambda: hub_tmp)
    monkeypatch.setattr(staging_store, "get_hub_dir", lambda: hub_tmp)

    events_store.upsert_event(
        DistilledNewsEvent(
            event_id="evt:distilled1",
            ticker="NIFTY",
            title="Distilled FII story",
            content="Foreign investors sold heavily.",
            publish_day="2026-04-28",
            published_at="2026-04-28",
            verification_status="approved",
        )
    )
    staging_store.enqueue_raw_ref(
        {
            "title": "Pending staging headline",
            "summary": "Not yet distilled",
            "url": "https://example.com/staging-only",
            "published_at": "2026-04-28",
        },
        ticker="NIFTY",
    )

    rows = query_verified_news(ticker="NIFTY", publish_day="2026-04-28", limit=10)
    titles = {str(r.get("title") or "") for r in rows}
    assert "Distilled FII story" in titles
    assert "Pending staging headline" not in titles


def test_legacy_ingest_flag(monkeypatch):
    from trade_integrations.hub_storage import news_staging_store as staging_store

    monkeypatch.delenv("HUB_NEWS_LEGACY_INGEST", raising=False)
    assert staging_store.is_legacy_ingest_enabled() is False
    monkeypatch.setenv("HUB_NEWS_LEGACY_INGEST", "1")
    assert staging_store.is_legacy_ingest_enabled() is True


@pytest.fixture
def hub_tmp(tmp_path, monkeypatch):
    from trade_integrations.context import hub as hub_mod

    hub = tmp_path / "hub"
    hub.mkdir()
    monkeypatch.setattr(hub_mod, "get_hub_dir", lambda: hub)
    return hub
