"""Tests for hub news event_index.parquet materializer."""

from __future__ import annotations

import pytest


@pytest.fixture
def hub_tmp(tmp_path, monkeypatch):
    from trade_integrations.context import hub as hub_mod

    hub = tmp_path / "hub"
    hub.mkdir()
    monkeypatch.setattr(hub_mod, "get_hub_dir", lambda: hub)
    return hub


def test_query_index_excludes_rejected(hub_tmp):
    from trade_integrations.hub_storage import news_event_index as index
    from trade_integrations.hub_storage.news_event_models import DistilledNewsEvent

    index.upsert_index_from_event(
        DistilledNewsEvent(
            event_id="evt:rejected",
            ticker="NIFTY",
            title="Rejected story",
            content="Body",
            publish_day="2026-07-16",
            verification_status="rejected",
        )
    )
    index.upsert_index_from_event(
        DistilledNewsEvent(
            event_id="evt:approved",
            ticker="NIFTY",
            title="Approved story",
            content="Body",
            publish_day="2026-07-16",
            verification_status="approved",
        )
    )
    ids = {c.get("event_id") for c in index.query_index_candidates(ticker="NIFTY", publish_day="2026-07-16")}
    assert "evt:approved" in ids
    assert "evt:rejected" not in ids


def test_upsert_index_from_event(hub_tmp):
    from trade_integrations.hub_storage import news_event_index as index
    from trade_integrations.hub_storage.news_event_models import DistilledNewsEvent

    event = DistilledNewsEvent(
        event_id="evt:index1",
        ticker="NIFTY",
        title="RBI holds rates",
        content="Policy unchanged; markets steady.",
        publish_day="2026-07-16",
        tags={"topics": ["rbi", "rates"]},
        published_at="2026-07-16T10:00:00+00:00",
    )
    index.upsert_index_from_event(event)
    candidates = index.query_index_candidates(ticker="NIFTY", publish_day="2026-07-16")
    ids = {c.get("event_id") for c in candidates}
    assert "evt:index1" in ids
    assert candidates[0]["event_id"] == "evt:index1" or len(ids) == 1
    assert "RBI" in candidates[0]["title"]


def test_rebuild_event_index_merges_other_tickers(hub_tmp):
    from trade_integrations.hub_storage import news_event_index as index
    from trade_integrations.hub_storage import news_events_store as events_store
    from trade_integrations.hub_storage.news_event_models import DistilledNewsEvent

    events_store.upsert_event(
        DistilledNewsEvent(
            event_id="evt:nifty",
            ticker="NIFTY",
            title="Nifty move",
            content="Index up.",
            publish_day="2026-07-18",
        )
    )
    events_store.upsert_event(
        DistilledNewsEvent(
            event_id="evt:reliance",
            ticker="RELIANCE",
            title="Reliance results",
            content="Beat estimates.",
            publish_day="2026-07-18",
        )
    )
    index.rebuild_event_index(ticker="NIFTY")
    index.rebuild_event_index(ticker="RELIANCE")
    candidates = index.query_index_candidates(ticker="NIFTY", publish_day="2026-07-18")
    assert any(c.get("event_id") == "evt:nifty" for c in candidates)
    rel = index.query_index_candidates(ticker="RELIANCE", publish_day="2026-07-18")
    assert any(c.get("event_id") == "evt:reliance" for c in rel)


def test_ensure_event_index_backfills_missing_ticker(hub_tmp):
    from trade_integrations.hub_storage import news_event_index as index
    from trade_integrations.hub_storage import news_events_store as events_store
    from trade_integrations.hub_storage.news_event_models import DistilledNewsEvent

    events_store.upsert_event(
        DistilledNewsEvent(
            event_id="evt:nifty-only",
            ticker="NIFTY",
            title="Only Nifty",
            content="Body",
            publish_day="2026-07-19",
        )
    )
    index.rebuild_event_index(ticker="NIFTY")
    events_store.upsert_event(
        DistilledNewsEvent(
            event_id="evt:reliance-late",
            ticker="RELIANCE",
            title="Late Reliance",
            content="Body",
            publish_day="2026-07-19",
        )
    )
    index.ensure_event_index(ticker="RELIANCE")
    rel = index.query_index_candidates(ticker="RELIANCE", publish_day="2026-07-19")
    assert any(c.get("event_id") == "evt:reliance-late" for c in rel)


def test_rebuild_event_index_from_events_store(hub_tmp):
    from trade_integrations.hub_storage import news_event_index as index
    from trade_integrations.hub_storage import news_events_store as events_store
    from trade_integrations.hub_storage.news_event_models import DistilledNewsEvent

    events_store.upsert_event(
        DistilledNewsEvent(
            event_id="evt:rebuild",
            ticker="NIFTY",
            title="FII selling",
            content="Foreign investors sold.",
            publish_day="2026-07-17",
            tags={"topics": ["fii"]},
        )
    )
    summary = index.rebuild_event_index(ticker="NIFTY")
    assert summary["indexed"] >= 1
    candidates = index.query_index_candidates(ticker="NIFTY", publish_day="2026-07-17")
    assert any(c.get("event_id") == "evt:rebuild" for c in candidates)
