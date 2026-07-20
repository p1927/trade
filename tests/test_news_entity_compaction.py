"""Tests for distilled events compaction."""

from __future__ import annotations

from datetime import date, timedelta
from unittest.mock import patch

import pytest

from trade_integrations.hub_storage import news_events_store as events_store
from trade_integrations.hub_storage.news_event_models import DistilledNewsEvent, EventConsensus


@pytest.fixture
def hub_tmp(tmp_path, monkeypatch):
    from trade_integrations.context import hub as hub_mod

    hub = tmp_path / "hub"
    hub.mkdir()
    monkeypatch.setattr(hub_mod, "get_hub_dir", lambda: hub)
    return hub


def _similar_event(event_id: str, *, title_suffix: str = "", publish_day: str | None = None) -> DistilledNewsEvent:
    day = publish_day or (date.today() - timedelta(days=1)).isoformat()
    body = (
        "Foreign investors sold Rs 2,500 crore in the cash segment on Thursday, "
        "dragging the Nifty lower by 120 points amid global risk-off sentiment."
    )
    tags = {
        "topics": ["fii"],
        "themes": ["selloff"],
        "factors": ["fii_net_5d"],
        "symbols": ["NIFTY"],
        "publish_day": day,
    }
    return DistilledNewsEvent(
        event_id=event_id,
        ticker="NIFTY",
        title=f"FII selling drags Nifty lower{title_suffix}",
        content=body,
        publish_day=day,
        published_at=f"{day}T10:00:00+00:00",
        tags=tags,
        consensus=EventConsensus(direction="bearish", ref_count=1, topics=["fii"], factors=["fii_net_5d"]),
        verification_status="approved",
    )


def test_compact_distilled_events_dry_run_merges_similar_pair(hub_tmp, monkeypatch):
    monkeypatch.setenv("HUB_NEWS_DEDUP_SUMMARY_THRESHOLD", "0.72")
    from trade_integrations.dataflows.index_research import news_entity_worker as worker

    monkeypatch.setattr(worker, "require_minimax_for_distillation", lambda: None)

    events_store.upsert_event(_similar_event("evt:a"))
    events_store.upsert_event(_similar_event("evt:b", title_suffix=" by 120 points"))
    assert events_store.count_events(ticker="NIFTY") == 2

    result = worker.compact_distilled_events(ticker="NIFTY", lookback_days=30, dry_run=True)
    assert result["groups_merged"] == 1
    assert result["rows_removed"] == 1
    assert events_store.count_events(ticker="NIFTY") == 2


def test_remove_events_and_distillation_log(hub_tmp):
    events_store.upsert_event(_similar_event("evt:keep"))
    events_store.upsert_event(_similar_event("evt:drop"))
    removed = events_store.remove_events({"evt:drop"})
    assert removed == 1
    assert events_store.get_event("evt:drop") is None

    events_store.append_distillation_log(
        {"reason": "test", "canonical_event_id": "evt:keep", "removed_event_ids": ["evt:drop"]}
    )
    log_path = events_store.distillation_log_path()
    assert log_path.is_file()
    assert "evt:drop" in log_path.read_text(encoding="utf-8")


def test_build_duplicate_group_uses_star_topology_not_transitive():
    from trade_integrations.dataflows.index_research.news_entity_worker import _build_duplicate_group

    anchor = {"canonical_story_id": "evt:a", "title": "Anchor", "content_summary": "Anchor body", "tags": {}}
    member_b = {"canonical_story_id": "evt:b", "title": "Member B", "content_summary": "B body", "tags": {}}
    member_c = {"canonical_story_id": "evt:c", "title": "Member C", "content_summary": "C body", "tags": {}}

    def _fake_match(ref, events, *, ticker="NIFTY", threshold=None):
        ref_title = ref.get("title") or ""
        event_id = str(events[0].get("canonical_story_id") or "")
        if ref_title == "Member B" and event_id == "evt:a":
            return events[0]
        if ref_title == "Member C" and event_id == "evt:b":
            return events[0]
        return None

    with patch(
        "trade_integrations.dataflows.index_research.news_event_matching.find_matching_event",
        side_effect=_fake_match,
    ):
        group = _build_duplicate_group(
            anchor,
            [anchor, member_b, member_c],
            ticker="NIFTY",
            consumed=set(),
        )

    ids = {str(r.get("canonical_story_id") or "") for r in group}
    assert ids == {"evt:a", "evt:b"}
    assert "evt:c" not in ids


def test_run_hub_news_entity_job_drain_mode_skips_maintenance(monkeypatch):
    from trade_integrations.dataflows.index_research import news_entity_worker as worker

    monkeypatch.setattr(
        worker,
        "process_staging_batch",
        lambda **_: {"processed": 2, "created": 1, "updated": 1},
    )
    monkeypatch.setattr(
        "trade_integrations.hub_storage.news_staging_store.pipeline_pause_status",
        lambda **_: {"pipeline_paused": False, "pause_reason": "", "pending": {"queued": 0}},
    )
    monkeypatch.setattr(
        "trade_integrations.hub_storage.news_migrations.ensure_hub_news_migrations",
        lambda **_: {"status": "ok"},
    )

    def _should_not_run(*_a, **_k):
        raise AssertionError("maintenance stages must not run in drain mode")

    monkeypatch.setattr(worker, "repair_leaked_distilled_summaries", _should_not_run)
    monkeypatch.setattr(worker, "backfill_distilled_event_metadata", _should_not_run)
    monkeypatch.setattr(worker, "compact_distilled_events", _should_not_run)

    result = worker.run_hub_news_entity_job({"ticker": "NIFTY", "mode": "drain"})
    assert result.get("mode") == "drain"
    assert result.get("repair", {}).get("skipped") is True
    assert result.get("staging", {}).get("processed") == 2


def test_run_hub_news_entity_job_skips_repair_when_pipeline_paused(monkeypatch):
    from trade_integrations.dataflows.index_research import news_entity_worker as worker

    monkeypatch.setattr(
        worker,
        "process_staging_batch",
        lambda **_: {"processed": 0, "pipeline_paused": True},
    )
    monkeypatch.setattr(
        "trade_integrations.hub_storage.news_staging_store.pipeline_pause_status",
        lambda **_: {
            "pipeline_paused": True,
            "pause_reason": "MINIMAX_API_KEY is not configured.",
            "pending": {"queued": 3},
        },
    )

    def _should_not_run(*_a, **_k):
        raise AssertionError("repair/compact must not run when pipeline is paused")

    monkeypatch.setattr(worker, "repair_leaked_distilled_summaries", _should_not_run)
    monkeypatch.setattr(worker, "backfill_distilled_event_metadata", _should_not_run)
    monkeypatch.setattr(worker, "compact_distilled_events", _should_not_run)

    result = worker.run_hub_news_entity_job({"ticker": "NIFTY"})
    assert result.get("pipeline_paused") is True
    assert result.get("repair", {}).get("skipped") is True
    assert result.get("compact_events", {}).get("skipped") is True


def test_enqueue_respects_backpressure(hub_tmp, monkeypatch):
    from trade_integrations.hub_storage import news_staging_store as staging

    monkeypatch.setattr(staging, "entity_backpressure_threshold", lambda: 2)
    _, first = staging.enqueue_raw_ref({"title": "a", "url": "http://example.com/a"}, ticker="NIFTY")
    _, second = staging.enqueue_raw_ref({"title": "b", "url": "http://example.com/b"}, ticker="NIFTY")
    _, third = staging.enqueue_raw_ref({"title": "c", "url": "http://example.com/c"}, ticker="NIFTY")
    assert first is True
    assert second is True
    assert third is False


def test_adaptive_drain_batch_size(monkeypatch):
    from trade_integrations.dataflows.index_research import news_entity_worker as worker

    monkeypatch.setattr(
        "trade_integrations.hub_storage.news_staging_store.staging_queue_detail",
        lambda **_: {"queued": 800},
    )
    assert worker._adaptive_drain_batch_size(ticker="NIFTY") == 200


def test_run_hub_news_entity_job_uses_adaptive_batch(monkeypatch):
    from trade_integrations.dataflows.index_research import news_entity_worker as worker

    captured: dict[str, int] = {}

    def _fake_batch(**kwargs):
        captured["limit"] = int(kwargs.get("limit") or 0)
        return {"processed": 1, "created": 1, "updated": 0}

    monkeypatch.setattr(worker, "process_staging_batch", _fake_batch)
    monkeypatch.setattr(
        "trade_integrations.hub_storage.news_staging_store.staging_queue_detail",
        lambda **_: {"queued": 1000},
    )
    monkeypatch.setattr(
        "trade_integrations.hub_storage.news_staging_store.pipeline_pause_status",
        lambda **_: {"pipeline_paused": False, "pause_reason": "", "pending": {"queued": 1000}},
    )
    monkeypatch.setattr(
        "trade_integrations.hub_storage.news_migrations.ensure_hub_news_migrations",
        lambda **_: {"status": "ok"},
    )

    worker.run_hub_news_entity_job(
        {"ticker": "NIFTY", "mode": "drain", "batch_size": "adaptive", "adaptive_batch": True}
    )
    assert captured["limit"] == 250
