"""Tests for unified hub news ingest orchestrator."""

from __future__ import annotations

import pytest


@pytest.fixture
def hub_tmp(tmp_path, monkeypatch):
    from trade_integrations.context import hub as hub_mod

    hub = tmp_path / "hub"
    hub.mkdir()
    monkeypatch.setattr(hub_mod, "get_hub_dir", lambda: hub)
    return hub


def test_parse_sources_all():
    from trade_integrations.dataflows.index_research.hub_news_ingest import _parse_sources

    assert _parse_sources("all") == {"rss", "searxng", "searxng_global", "moneycontrol", "watcher"}
    assert _parse_sources("rss,searxng") == {"rss", "searxng"}
    assert _parse_sources(["moneycontrol", "invalid"]) == {"moneycontrol"}


def test_run_hub_news_ingest_rss_only(hub_tmp, monkeypatch):
    from trade_integrations.hub_storage import news_staging_store as staging_store

    monkeypatch.setattr(staging_store, "get_hub_dir", lambda: hub_tmp)

    def fake_rss(**kwargs):
        from trade_integrations.dataflows.news_hub_bridge import ingest_rss_entries

        entries = [
            {
                "title": "Nifty rises on FII inflows",
                "summary": "Markets up",
                "date": "2026-07-20",
                "url": "https://example.com/nifty-up",
            }
        ]
        return ingest_rss_entries(entries, ticker=kwargs["ticker"], label="test", feed_url="https://x")

    monkeypatch.setattr(
        "trade_integrations.dataflows.index_research.hub_news_ingest._ingest_rss",
        fake_rss,
    )
    monkeypatch.setattr(
        "trade_integrations.dataflows.hub_wiki.client.health_check",
        lambda: {"ok": True},
    )

    from trade_integrations.dataflows.index_research.hub_news_ingest import run_hub_news_ingest

    result = run_hub_news_ingest(ticker="NIFTY", sources="rss")
    assert "rss" in result["sources"]
    assert result["totals"]["queued"] >= 1
    assert staging_store.list_pending_refs(ticker="NIFTY", limit=5)


def test_hub_ingest_snapshot(hub_tmp, monkeypatch):
    from trade_integrations.hub_storage import news_staging_store as staging_store

    monkeypatch.setattr(staging_store, "get_hub_dir", lambda: hub_tmp)
    staging_store.enqueue_raw_ref(
        {
            "title": "Test",
            "summary": "Body",
            "url": "https://example.com/snap",
            "published_at": "2026-07-20",
        },
        ticker="RELIANCE",
    )

    from trade_integrations.dataflows.index_research.hub_news_ingest import hub_ingest_snapshot

    snap = hub_ingest_snapshot(ticker="RELIANCE")
    assert snap["ticker"] == "RELIANCE"
    assert snap["scope"] == "micro"
    assert snap["staging"]["queued"] >= 1
