"""Tests for hub verified news store."""

from __future__ import annotations

import json

import pytest

from trade_integrations.hub_storage import verified_news_store as store


@pytest.fixture
def hub_tmp(tmp_path, monkeypatch):
    hub = tmp_path / "hub"
    hub.mkdir()
    monkeypatch.setattr(store, "get_hub_dir", lambda: hub)
    return hub


def test_upsert_and_get_round_trip(hub_tmp):
    store.upsert_verified_record(
        {
            "canonical_story_id": "title:nifty falls on fii selling",
            "ticker": "NIFTY",
            "title": "Nifty falls on FII selling",
            "content_summary": "FII sold Rs 2000 cr over five sessions.",
            "structured_summary": {"facts": ["FII outflows"], "entities": ["FII"], "implied_factors": ["fii_net_5d"]},
            "sources": [{"vendor": "rss", "publisher": "ET", "url": "https://example.com/a"}],
            "published_at": "2026-02-17T10:00:00+00:00",
            "verification_status": "approved",
            "verification": {"status": "approved", "claims": []},
            "verification_data_as_of": "2026-02-17",
        }
    )
    rec = store.get_verified_record("title:nifty falls on fii selling")
    assert rec is not None
    assert rec["verification_status"] == "approved"
    assert len(rec["sources"]) == 1


def test_upsert_merges_sources_and_longer_summary(hub_tmp):
    story_id = "url:example.com.story"
    store.upsert_verified_record(
        {
            "canonical_story_id": story_id,
            "title": "Oil surge",
            "content_summary": "Short.",
            "sources": [{"vendor": "rss", "publisher": "A", "url": "https://example.com/1"}],
            "published_at": "2026-02-17",
            "verification_status": "partial",
            "verification": {"status": "partial"},
            "verification_data_as_of": "2026-02-17",
        }
    )
    store.upsert_verified_record(
        {
            "canonical_story_id": story_id,
            "title": "Oil surge hits markets",
            "content_summary": "Brent jumped 4% after supply fears intensified across Asia.",
            "sources": [{"vendor": "aggregator", "publisher": "B", "url": "https://example.com/2"}],
            "published_at": "2026-02-17",
            "verification_status": "partial",
            "verification": {"status": "partial"},
            "verification_data_as_of": "2026-02-17",
        }
    )
    rec = store.get_verified_record(story_id)
    assert rec is not None
    assert len(rec["sources"]) == 2
    assert "Brent jumped" in rec["content_summary"]


def test_rejected_records_persist(hub_tmp):
    store.upsert_verified_record(
        {
            "canonical_story_id": "title:fake crash headline",
            "title": "Nifty to crash 30%",
            "content_summary": "Clickbait only.",
            "verification_status": "rejected",
            "verification": {"status": "rejected", "approval_note": "contradicted"},
            "verification_data_as_of": "2026-02-17",
            "published_at": "2026-02-17",
        }
    )
    rejected = store.list_verified_records(status="rejected", include_rejected=True)
    assert len(rejected) == 1
    approved = store.list_verified_records()
    assert len(approved) == 0


def test_build_snapshot_from_hub(hub_tmp):
    store.upsert_verified_record(
        {
            "canonical_story_id": "title:approved story",
            "title": "Approved story",
            "content_summary": "Body",
            "verification_status": "approved",
            "verification": {"status": "approved"},
            "verification_data_as_of": "2026-02-17",
            "published_at": "2026-07-16",
            "predicted_impact": {"return_pct": -1.0, "nifty_points": -250},
        }
    )
    snap = store.build_snapshot_from_hub(ticker="NIFTY", horizon_days=14, spot=25000)
    assert snap["summary"]["source"] == "hub_records"
    assert len(snap["items"]) == 1
