"""Tests for hub news records→events migrations."""

from __future__ import annotations

import pytest

from trade_integrations.hub_storage import news_events_store as events_store
from trade_integrations.hub_storage import news_migrations as migrations
from trade_integrations.hub_storage import verified_news_store as verified_store


@pytest.fixture
def hub_tmp(tmp_path, monkeypatch):
    from trade_integrations.context import hub as hub_mod

    hub = tmp_path / "hub"
    hub.mkdir()
    monkeypatch.setattr(hub_mod, "get_hub_dir", lambda: hub)
    return hub


def _legacy_record(story_id: str, *, ticker: str = "NIFTY", summary: str | None = None) -> dict:
    body = summary or "Foreign investors sold heavily in cash segment."
    return {
        "canonical_story_id": story_id,
        "ticker": ticker,
        "title": "FII selling drags Nifty lower",
        "content_summary": body,
        "published_at": "2026-07-16T10:00:00+00:00",
        "verification_status": "approved",
        "verification": {"status": "approved"},
        "verification_data_as_of": "2026-07-16",
        "tags": {"topics": ["fii"], "publish_day": "2026-07-16"},
        "sources": [{"vendor": "rss", "publisher": "ET", "url": "https://example.com/a"}],
        "structured_summary": {
            "facts": ["FII outflows"],
            "event_meta": {"event_id": story_id, "distilled": True},
        },
    }


def test_migrate_records_to_events_from_legacy(hub_tmp):
    verified_store.seed_legacy_record(_legacy_record("evt:migrate1"))
    assert events_store.count_events(ticker="NIFTY") == 0
    assert migrations.needs_news_migration(ticker="NIFTY") is True

    result = migrations.migrate_records_to_events(ticker="NIFTY", only_missing=False)
    assert result["upserted"] == 1
    assert events_store.count_events(ticker="NIFTY") == 1
    assert events_store.get_event("evt:migrate1") is not None


def test_finalize_events_ssot_archives_legacy(hub_tmp):
    verified_store.seed_legacy_record(_legacy_record("evt:cutover"))
    summary = migrations.finalize_events_ssot()
    assert summary["upserted"] == 1
    assert summary["archive"].get("archived") is True
    assert events_store.count_events(ticker="NIFTY") == 1
    assert migrations.needs_news_migration() is False
    assert migrations.load_migration_state().get("legacy_archived") is True


def test_ensure_hub_news_migrations_is_idempotent(hub_tmp):
    verified_store.seed_legacy_record(_legacy_record("evt:idempotent"))
    first = migrations.ensure_hub_news_migrations(ticker="NIFTY")
    second = migrations.ensure_hub_news_migrations(ticker="NIFTY")

    assert first.get("finalize", {}).get("upserted", 0) >= 1 or events_store.count_events(ticker="NIFTY") >= 1
    assert second.get("incremental", {}).get("skipped") is True
    assert migrations.needs_news_migration(ticker="NIFTY") is False


def test_migrate_all_tickers(hub_tmp):
    verified_store.seed_legacy_record(_legacy_record("evt:nifty", ticker="NIFTY"))
    verified_store.seed_legacy_record(_legacy_record("evt:bank", ticker="BANKNIFTY"))

    summary = migrations.finalize_events_ssot()
    assert summary["upserted"] == 2
    assert events_store.count_events(ticker="NIFTY") == 1
    assert events_store.count_events(ticker="BANKNIFTY") == 1


def test_resync_when_legacy_content_changes(hub_tmp):
    verified_store.seed_legacy_record(_legacy_record("evt:resync"))
    migrations.migrate_records_to_events(ticker="NIFTY", only_missing=False)

    verified_store.seed_legacy_record(
        _legacy_record(
            "evt:resync",
            summary=(
                "Updated body after revision with more detail on sustained FII outflows "
                "and weaker close for the benchmark index."
            ),
        )
    )
    result = migrations.migrate_records_to_events(ticker="NIFTY", only_missing=True, resync_stale=True)
    assert result["resynced"] == 1
    assert "Updated body" in str((events_store.get_event("evt:resync") or {}).get("content") or "")
