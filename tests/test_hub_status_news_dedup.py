"""Tests for hub status news inventory staging/distilled dedup."""

from __future__ import annotations

import pytest


@pytest.fixture
def hub_tmp(tmp_path, monkeypatch):
    from trade_integrations.context import hub as hub_mod
    from trade_integrations.hub_storage import news_staging_store as staging
    from trade_integrations.hub_storage import verified_news_store as store

    hub = tmp_path / "hub"
    hub.mkdir()
    monkeypatch.setattr(hub_mod, "get_hub_dir", lambda: hub)
    monkeypatch.setattr(staging, "get_hub_dir", lambda: hub)
    monkeypatch.setattr(store, "get_hub_dir", lambda: hub)
    return hub


def test_recent_news_inventory_skips_staging_when_url_in_distilled(hub_tmp):
    from trade_integrations.hub_storage import news_staging_store as staging
    from trade_integrations.hub_storage import verified_news_store as store
    from trade_integrations.hub_storage.hub_status import _recent_news_inventory

    store.upsert_verified_record(
        {
            "canonical_story_id": "url:news.example.com/fii-story",
            "ticker": "NIFTY",
            "title": "Distilled FII story",
            "content_summary": "Foreign investors sold.",
            "sources": [{"vendor": "rss", "url": "https://news.example.com/fii-story/", "publisher": "rss"}],
            "published_at": "2026-04-28T10:00:00+00:00",
            "verification_status": "approved",
            "tags": {"topics": ["fii"], "themes": [], "factors": [], "symbols": []},
        }
    )
    staging.enqueue_raw_ref(
        {
            "title": "Same story pending distill",
            "summary": "Duplicate URL variant",
            "url": "https://news.example.com/fii-story",
            "published_at": "2026-04-28T10:00:00+00:00",
        },
        ticker="NIFTY",
    )
    staging.enqueue_raw_ref(
        {
            "title": "Unique staging only",
            "summary": "Only in queue",
            "url": "https://news.example.com/staging-only",
            "published_at": "2026-04-28T11:00:00+00:00",
        },
        ticker="NIFTY",
    )

    inventory = _recent_news_inventory(ticker="NIFTY", limit=20)
    union_titles = {item.get("title") for item in inventory.get("items") or []}
    staging_titles = {item.get("title") for item in inventory.get("staging_queue") or []}

    assert "Distilled FII story" in union_titles
    assert "Unique staging only" in union_titles
    assert "Same story pending distill" not in staging_titles
    assert inventory.get("pending_count") == 2
