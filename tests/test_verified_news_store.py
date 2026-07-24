"""Tests for hub verified news store."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from trade_integrations.hub_storage import verified_news_store as store


def _patch_hub_dir(monkeypatch, hub: Path) -> None:
    for target in (
        "trade_integrations.context.hub.get_hub_dir",
        "trade_integrations.hub_storage.news_events_store.get_hub_dir",
        "trade_integrations.hub_storage.verified_news_store.get_hub_dir",
        "trade_integrations.hub_storage.news_event_index.get_hub_dir",
    ):
        monkeypatch.setattr(target, lambda _hub=hub: _hub)


@pytest.fixture
def hub_tmp(tmp_path, monkeypatch):
    from trade_integrations.context import hub as hub_mod

    hub = tmp_path / "hub"
    hub.mkdir()
    _patch_hub_dir(monkeypatch, hub)
    monkeypatch.setattr(hub_mod, "get_hub_dir", lambda: hub)
    return hub


def _two_sources(prefix: str = "https://example.com") -> list[dict[str, str]]:
    return [
        {"vendor": "rss", "publisher": "A", "url": f"{prefix}/1"},
        {"vendor": "rss", "publisher": "B", "url": f"{prefix}/2"},
    ]


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
            "sources": _two_sources(),
            "verification_status": "approved",
            "verification": {"status": "approved"},
            "verification_data_as_of": "2026-02-17",
            "published_at": "2026-07-16",
            "predicted_impact": {"return_pct": -1.0, "nifty_points": -250},
        }
    )
    snap = store.build_snapshot_from_hub(ticker="NIFTY", horizon_days=14, spot=25000, prediction_date="2026-07-16")
    assert snap["summary"]["source"] == "hub_events"
    assert len(snap["items"]) == 1
    assert snap.get("prediction_date") == "2026-07-16"
    assert "prediction_attribution" in snap["items"][0]
