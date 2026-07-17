"""Tests for hub distilled news events store."""

from __future__ import annotations

import pytest

from trade_integrations.hub_storage import news_events_store as events_store
from trade_integrations.hub_storage.news_event_models import DistilledNewsEvent


@pytest.fixture
def hub_tmp(tmp_path, monkeypatch):
    from trade_integrations.context import hub as hub_mod

    hub = tmp_path / "hub"
    hub.mkdir()
    monkeypatch.setattr(hub_mod, "get_hub_dir", lambda: hub)
    return hub


def test_upsert_and_query_event(hub_tmp):
    evt = DistilledNewsEvent(
        event_id="evt:test1",
        ticker="NIFTY",
        title="FII selling weighs on Nifty",
        content="Foreign investors sold for a third session…",
        publish_day="2026-04-28",
        published_at="2026-04-28T10:00:00+00:00",
    )
    events_store.upsert_event(evt)
    rows = events_store.list_events(ticker="NIFTY", since="2026-04-28")
    assert rows[0]["event_id"] == "evt:test1"
    assert "FII" in rows[0]["title"]


def test_get_event_round_trip(hub_tmp):
    evt = DistilledNewsEvent(
        event_id="evt:roundtrip",
        ticker="NIFTY",
        title="Oil spike",
        content="Brent rose 3% on supply fears.",
        publish_day="2026-04-29",
    )
    events_store.upsert_event(evt)
    loaded = events_store.get_event("evt:roundtrip")
    assert loaded is not None
    assert loaded["title"] == "Oil spike"


def test_distilled_event_to_headline_dict(hub_tmp):
    evt = DistilledNewsEvent(
        event_id="evt:headline",
        ticker="NIFTY",
        title="Markets flat",
        content="Nifty ended unchanged.",
        publish_day="2026-04-30",
        verification_status="approved",
    )
    events_store.upsert_event(evt)
    row = events_store.get_event("evt:headline")
    headline = events_store.distilled_event_to_headline_dict(row or {})
    assert headline["canonical_story_id"] == "evt:headline"
    assert headline["content_summary"] == "Nifty ended unchanged."
    assert headline["provenance"] == "distilled_event"


def test_event_from_verified_record(hub_tmp):
    legacy = {
        "canonical_story_id": "title:nifty falls",
        "ticker": "NIFTY",
        "title": "Nifty falls on FII selling",
        "content_summary": "FII sold Rs 2000 cr.",
        "published_at": "2026-04-28T10:00:00+00:00",
        "verification_status": "approved",
        "tags": {"topics": ["flows"], "publish_day": "2026-04-28"},
        "sources": [{"vendor": "rss", "publisher": "ET", "url": "https://example.com/a"}],
        "structured_summary": {
            "facts": ["FII outflows"],
            "event_meta": {"event_id": "evt:legacy1", "distilled": True},
        },
    }
    event = events_store.event_from_verified_record(legacy)
    assert event.event_id == "evt:legacy1"
    assert event.title == "Nifty falls on FII selling"
    events_store.upsert_event(event)
    assert events_store.count_events(ticker="NIFTY") == 1
