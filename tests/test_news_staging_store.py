"""Tests for hub news staging queue."""

from __future__ import annotations

import pytest

from trade_integrations.hub_storage import news_staging_store as staging


@pytest.fixture
def hub_tmp(tmp_path, monkeypatch):
    from trade_integrations.context import hub as hub_mod

    hub = tmp_path / "hub"
    hub.mkdir()
    monkeypatch.setattr(hub_mod, "get_hub_dir", lambda: hub)
    return hub


def test_enqueue_and_list_pending_ref(hub_tmp):
    ref_id, appended = staging.enqueue_raw_ref(
        {"title": "FII sell", "summary": "Outflows continue", "url": "https://x/a"},
        ticker="NIFTY",
    )
    assert appended is True
    pending = staging.list_pending_refs(ticker="NIFTY")
    assert len(pending) == 1
    assert pending[0]["ref_id"] == ref_id
    assert pending[0]["status"] == "queued"

    staging.mark_ref_merged(ref_id, "evt:abc")
    assert staging.list_pending_refs(ticker="NIFTY") == []


def test_enqueue_skips_duplicate_url(hub_tmp):
    row = {"title": "FII sell", "summary": "Outflows continue", "url": "https://x/a"}
    first_id, first_appended = staging.enqueue_raw_ref(row, ticker="NIFTY")
    second_id, second_appended = staging.enqueue_raw_ref(row, ticker="NIFTY")
    assert first_appended is True
    assert second_appended is False
    assert first_id == second_id
    assert len(staging.list_pending_refs(ticker="NIFTY")) == 1


def test_enqueue_skips_url_with_trailing_slash(hub_tmp):
    _, first_appended = staging.enqueue_raw_ref(
        {"title": "Story", "summary": "Body", "url": "https://news.example.com/story"},
        ticker="NIFTY",
    )
    _, second_appended = staging.enqueue_raw_ref(
        {"title": "Story copy", "summary": "Body", "url": "https://news.example.com/story/"},
        ticker="NIFTY",
    )
    assert first_appended is True
    assert second_appended is False
    assert len(staging.list_pending_refs(ticker="NIFTY")) == 1


def test_collect_distilled_urls_normalizes(hub_tmp):
    urls = staging.collect_distilled_urls(
        [
            {
                "url": "https://News.Example.com/Story/",
                "sources": [{"url": "https://other.example.com/x"}],
            }
        ]
    )
    assert "news.example.com/story" in urls
    assert "other.example.com/x" in urls


def test_staging_ref_to_headline_shape(hub_tmp):
    ref_id, _ = staging.enqueue_raw_ref(
        {
            "title": "RBI holds rates",
            "summary": "Policy unchanged",
            "url": "https://x/rbi",
            "published_at": "2026-04-28T10:00:00+00:00",
        },
        ticker="NIFTY",
    )
    pending = staging.list_pending_refs(ticker="NIFTY")[0]
    headline = staging.staging_ref_to_headline(pending)
    assert headline["canonical_story_id"] == ref_id
    assert headline["verification_status"] == "pending"
    assert headline["provenance"] == "staging"
