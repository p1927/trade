"""Tests for manual discard and discard-similar."""

from __future__ import annotations

import pytest


@pytest.fixture
def hub_tmp(tmp_path, monkeypatch):
    from trade_integrations.context import hub as hub_mod

    hub = tmp_path / "hub"
    hub.mkdir()
    monkeypatch.setattr(hub_mod, "get_hub_dir", lambda: hub)
    return hub


def test_discard_staging_ref(hub_tmp, monkeypatch):
    from trade_integrations.hub_storage import news_staging_store as staging
    from trade_integrations.dataflows.index_research.news_discard import discard_news_item

    monkeypatch.setattr(staging, "get_hub_dir", lambda: hub_tmp)
    ref_id, _ = staging.enqueue_raw_ref(
        {"title": "Test headline", "summary": "Body", "url": "https://example.com/a"},
        ticker="NIFTY",
    )
    result = discard_news_item(ref_id, ticker="NIFTY", source_kind="staging")
    assert result.get("count") == 1
    assert staging.list_pending_refs(ticker="NIFTY") == []
    assert staging.discarded_count(ticker="NIFTY") == 1


def test_discard_similar_with_mocked_similarity(hub_tmp, monkeypatch):
    from trade_integrations.hub_storage import news_staging_store as staging
    from trade_integrations.dataflows.index_research import news_discard as discard_mod

    monkeypatch.setattr(staging, "get_hub_dir", lambda: hub_tmp)
    staging.enqueue_raw_ref(
        {
            "title": "FII sell-off hits Nifty",
            "summary": "Foreign investors sold heavily",
            "url": "https://example.com/fii1",
            "tags": {"topics": ["fii"], "factors": ["fii_net_5d"]},
        },
        ticker="NIFTY",
    )
    staging.enqueue_raw_ref(
        {
            "title": "FII outflow weighs on Nifty 50",
            "summary": "Continued foreign selling",
            "url": "https://example.com/fii2",
            "tags": {"topics": ["fii"], "factors": ["fii_net_5d"]},
        },
        ticker="NIFTY",
    )
    refs = staging.list_pending_refs(ticker="NIFTY")
    anchor = refs[0]

    monkeypatch.setattr(
        discard_mod,
        "_similarity",
        lambda a, b: 0.9,
    )
    result = discard_mod.discard_similar_items(anchor, ticker="NIFTY")
    assert result.get("discarded_count", 0) >= 2
    assert staging.list_pending_refs(ticker="NIFTY") == []


def test_cleanup_purge_expired(hub_tmp, monkeypatch):
    from trade_integrations.hub_storage import news_staging_store as staging
    from trade_integrations.dataflows.index_research.news_cleanup import cleanup_hub_news

    monkeypatch.setattr(staging, "get_hub_dir", lambda: hub_tmp)
    row = staging.append_discarded_record(
        source_kind="manual",
        ticker="NIFTY",
        title="Old junk",
        url="https://example.com/old",
        reason="test",
        restore_payload={"ref_id": "ref:old"},
    )
    path = hub_tmp / "_data" / "news_staging" / "discarded.jsonl"
    text = path.read_text(encoding="utf-8")
    text = text.replace(str(row["expires_at"]), "2020-01-01T00:00:00+00:00")
    path.write_text(text, encoding="utf-8")

    stats = cleanup_hub_news(ticker="NIFTY")
    assert stats.get("purged_discarded", 0) >= 1
