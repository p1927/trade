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


def _similar_event(
    event_id: str,
    *,
    title_suffix: str = "",
    publish_day: str | None = None,
    parent_event_id: str = "parent:fii:2026",
) -> DistilledNewsEvent:
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
        parent_event_id=parent_event_id,
        structured_summary={"event_meta": {"parent_event_id": parent_event_id}},
        tags=tags,
        consensus=EventConsensus(direction="bearish", ref_count=1, topics=["fii"], factors=["fii_net_5d"]),
        verification_status="approved",
    )


def test_compact_distilled_events_skips_similarity_only_without_second_signal(hub_tmp, monkeypatch):
    monkeypatch.setenv("HUB_NEWS_DEDUP_SUMMARY_THRESHOLD", "0.72")
    from trade_integrations.dataflows.index_research import news_entity_worker as worker

    monkeypatch.setattr(worker, "require_minimax_for_distillation", lambda: None)

    left = _similar_event("evt:a", parent_event_id="")
    left.parent_event_id = None
    left.structured_summary = {}
    right = _similar_event("evt:b", title_suffix=" by 120 points", parent_event_id="")
    right.parent_event_id = None
    right.structured_summary = {}
    events_store.upsert_event(left)
    events_store.upsert_event(right)

    result = worker.compact_distilled_events(ticker="NIFTY", lookback_days=30, dry_run=True)
    assert result["groups_merged"] == 0
    assert events_store.count_events(ticker="NIFTY") == 2


def test_post_upsert_safety_merges_shared_parent_pair(hub_tmp, monkeypatch):
    monkeypatch.setenv("HUB_NEWS_RULE_FALLBACK_DISTILL", "1")
    monkeypatch.setenv("MINIMAX_API_KEY", "")
    monkeypatch.setenv("HUB_NEWS_POST_UPSERT_SAFETY_SCAN", "1")
    from trade_integrations.dataflows.index_research.news_post_upsert_safety import (
        run_post_upsert_safety_scan,
    )

    events_store.upsert_event(_similar_event("evt:existing"))
    events_store.upsert_event(_similar_event("evt:fresh", title_suffix=" update"))
    assert events_store.count_events(ticker="NIFTY") == 2

    result = run_post_upsert_safety_scan("evt:fresh", ticker="NIFTY", dry_run=False)
    assert result.get("groups_merged") == 1
    assert events_store.count_events(ticker="NIFTY") == 1
    surviving = events_store.get_event("evt:existing") or events_store.get_event("evt:fresh")
    assert surviving is not None


def test_post_upsert_safety_skipped_when_disabled(hub_tmp, monkeypatch):
    monkeypatch.setenv("HUB_NEWS_POST_UPSERT_SAFETY_SCAN", "0")
    from trade_integrations.dataflows.index_research.news_post_upsert_safety import (
        run_post_upsert_safety_scan,
    )

    events_store.upsert_event(_similar_event("evt:a"))
    result = run_post_upsert_safety_scan("evt:a", ticker="NIFTY")
    assert result.get("skipped") is True
    assert result.get("reason") == "disabled"


def test_resolver_agent_enabled_by_default(monkeypatch):
    from trade_integrations.dataflows.index_research import news_resolver_agent as agent_mod

    monkeypatch.delenv("HUB_NEWS_RESOLVER_AGENT_ENABLED", raising=False)
    monkeypatch.setenv("MINIMAX_API_KEY", "test-key")
    assert agent_mod.resolver_agent_enabled() is True
    monkeypatch.setenv("HUB_NEWS_RESOLVER_AGENT_ENABLED", "0")
    assert agent_mod.resolver_agent_enabled() is False


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


def test_distill_event_uses_canonical_id_on_create(monkeypatch):
    monkeypatch.setenv("HUB_NEWS_RULE_FALLBACK_DISTILL", "1")
    monkeypatch.setenv("MINIMAX_API_KEY", "")
    from trade_integrations.dataflows.index_research.news_distillation import distill_event

    refs = [
        {
            "title": "FII selling drags Nifty",
            "summary": "Foreign investors sold heavily.",
            "url": "https://news.example.com/fii",
            "source": "rss",
            "published_at": "2026-04-28T10:00:00+00:00",
        }
    ]
    out = distill_event(refs=refs, previous=None, canonical_event_id="url:news.example.com/fii")
    em = (out.get("structured_summary") or {}).get("event_meta") or {}
    assert em.get("event_id") == "url:news.example.com/fii"


def test_build_duplicate_group_uses_star_topology_not_transitive():
    from trade_integrations.dataflows.index_research.news_entity_worker import _build_duplicate_group

    parent = "parent:fii:2026"
    anchor = {
        "canonical_story_id": "evt:a",
        "title": "Anchor",
        "content_summary": "Anchor body",
        "tags": {},
        "structured_summary": {"event_meta": {"parent_event_id": parent}},
    }
    member_b = {
        "canonical_story_id": "evt:b",
        "title": "Member B",
        "content_summary": "B body",
        "tags": {},
        "structured_summary": {"event_meta": {"parent_event_id": parent}},
    }
    member_c = {
        "canonical_story_id": "evt:c",
        "title": "Member C",
        "content_summary": "C body",
        "tags": {},
        "structured_summary": {"event_meta": {"parent_event_id": "parent:other:2026"}},
    }

    def _fake_two_signal(left, right, **kwargs):
        left_id = str(left.get("canonical_story_id") or "")
        right_id = str(right.get("canonical_story_id") or "")
        if {left_id, right_id} == {"evt:a", "evt:b"}:
            return True, "shared_parent"
        if {left_id, right_id} == {"evt:b", "evt:c"}:
            return True, "shared_parent"
        return False, ""

    with patch(
        "trade_integrations.dataflows.index_research.news_event_clubbing.two_signal_merge_eligible",
        side_effect=_fake_two_signal,
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


def test_run_hub_news_entity_job_drain_mode_runs_light_compact(monkeypatch):
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
    monkeypatch.setattr(worker, "_tickers_with_pending_staging", lambda: [])

    compact_calls: list[dict] = []

    def _compact(**kwargs):
        compact_calls.append(kwargs)
        return {"groups_merged": 0, "rows_removed": 0}

    monkeypatch.setattr(worker, "compact_distilled_events", _compact)

    def _should_not_run(*_a, **_k):
        raise AssertionError("full maintenance stages must not run in drain mode")

    monkeypatch.setattr(worker, "repair_leaked_distilled_summaries", _should_not_run)
    monkeypatch.setattr(worker, "backfill_distilled_event_metadata", _should_not_run)

    result = worker.run_hub_news_entity_job({"ticker": "NIFTY", "mode": "drain"})
    assert result.get("mode") == "drain"
    assert result.get("repair", {}).get("skipped") is True
    assert result.get("staging", {}).get("processed") == 2
    assert compact_calls and compact_calls[0].get("lookback_days") == 7


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


def test_build_source_event_index_reads_frontmatter(hub_tmp, monkeypatch):
    from trade_integrations.dataflows.hub_wiki import ensure_llm_wiki_project
    from trade_integrations.dataflows.hub_wiki.search_dedup import (
        build_source_event_index,
        resolve_hit_to_event_id,
    )

    ensure_llm_wiki_project()
    news_dir = hub_tmp / "llm-wiki" / "raw" / "sources" / "news"
    news_dir.mkdir(parents=True, exist_ok=True)
    (news_dir / "fii-sell.md").write_text(
        "---\n"
        "event_id: evt:canonical\n"
        "title: FII selling drags Nifty lower\n"
        "publish_day: 2026-07-20\n"
        "content_fingerprint: abc\n"
        "---\n"
        "# body\n",
        encoding="utf-8",
    )

    index = build_source_event_index(news_dir=news_dir)
    assert "evt:canonical" in index["by_event_id"]
    hit = {"path": "raw/sources/news/fii-sell.md", "score": 0.9}
    assert resolve_hit_to_event_id(hit, index) == "evt:canonical"


def test_compact_distilled_events_wiki_pass_dry_run(hub_tmp, monkeypatch):
    monkeypatch.setenv("HUB_NEWS_DEDUP_SUMMARY_THRESHOLD", "0.72")
    from trade_integrations.dataflows.index_research import news_entity_worker as worker

    monkeypatch.setattr(worker, "require_minimax_for_distillation", lambda: None)

    day = (date.today() - timedelta(days=1)).isoformat()
    canonical = _similar_event("evt:canonical", publish_day=day)
    canonical.sources = [{"vendor": "rss", "url": "https://example.com/1", "publisher": "Mint"}]
    orphan = _similar_event("evt:orphan", title_suffix=" duplicate", publish_day=day)
    events_store.upsert_event(canonical)
    events_store.upsert_event(orphan)

    wiki_group = [
        events_store.distilled_event_to_headline_dict(events_store.get_event("evt:canonical")),
        events_store.distilled_event_to_headline_dict(events_store.get_event("evt:orphan")),
    ]

    monkeypatch.setattr(
        "trade_integrations.dataflows.hub_wiki.search_dedup.wiki_search_available",
        lambda *a, **k: True,
    )
    monkeypatch.setattr(
        "trade_integrations.dataflows.hub_wiki.search_dedup.build_duplicate_groups_wiki",
        lambda *a, **k: ([(wiki_group, "evt:canonical")], {"wiki_search_queries": 1, "wiki_hits": 1}),
    )
    monkeypatch.setattr(
        "trade_integrations.dataflows.index_research.news_event_clubbing.build_duplicate_groups_two_signal",
        lambda *a, **k: [],
    )

    result = worker.compact_distilled_events(ticker="NIFTY", lookback_days=30, dry_run=True)
    assert result["wiki_groups_merged"] == 1
    assert result["wiki_search_queries"] == 1
    assert result["groups_merged"] >= 1
    assert events_store.count_events(ticker="NIFTY") == 2


def test_compact_distilled_events_wiki_merge_removes_wiki_files(hub_tmp, monkeypatch):
    monkeypatch.setenv("HUB_NEWS_RULE_FALLBACK_DISTILL", "1")
    monkeypatch.setenv("MINIMAX_API_KEY", "")
    from trade_integrations.dataflows.index_research import news_entity_worker as worker

    monkeypatch.setattr(worker, "require_minimax_for_distillation", lambda: None)

    day = (date.today() - timedelta(days=1)).isoformat()
    canonical = _similar_event("evt:canonical", publish_day=day)
    canonical.sources = [{"vendor": "rss", "url": "https://example.com/1", "publisher": "Mint"}]
    orphan = _similar_event("evt:orphan", title_suffix=" duplicate", publish_day=day)
    events_store.upsert_event(canonical)
    events_store.upsert_event(orphan)

    removed_calls: list[str] = []

    def _fake_remove(event, *, rescan=False):
        removed_calls.append(str(event.get("event_id") or ""))
        return {"ok": True, "removed": ["fake.md"]}

    monkeypatch.setattr(
        "trade_integrations.dataflows.hub_wiki.compile.remove_event_wiki_files",
        _fake_remove,
    )
    monkeypatch.setattr(
        "trade_integrations.dataflows.hub_wiki.compile.compile_event_to_wiki",
        lambda *a, **k: {"ok": True},
    )
    monkeypatch.setattr(
        "trade_integrations.dataflows.hub_wiki.compile.wiki_compile_enabled",
        lambda: True,
    )
    monkeypatch.setattr(
        "trade_integrations.dataflows.hub_wiki.search_dedup.wiki_search_available",
        lambda *a, **k: True,
    )

    canon_dict = events_store.distilled_event_to_headline_dict(events_store.get_event("evt:canonical"))
    orphan_dict = events_store.distilled_event_to_headline_dict(events_store.get_event("evt:orphan"))
    wiki_group = [canon_dict, orphan_dict]

    monkeypatch.setattr(
        "trade_integrations.dataflows.hub_wiki.search_dedup.build_duplicate_groups_wiki",
        lambda *a, **k: ([(wiki_group, "evt:canonical")], {"wiki_search_queries": 1, "wiki_hits": 1}),
    )
    monkeypatch.setattr(
        "trade_integrations.dataflows.index_research.news_event_clubbing.build_duplicate_groups_two_signal",
        lambda *a, **k: [],
    )

    result = worker.compact_distilled_events(ticker="NIFTY", lookback_days=30, dry_run=False, max_passes=1)
    assert result["wiki_groups_merged"] == 1
    assert events_store.count_events(ticker="NIFTY") == 1
    assert "evt:orphan" in removed_calls


def test_hit_score_defaults_to_zero_without_api_score():
    from trade_integrations.dataflows.hub_wiki.search_dedup import _hit_score, score_wiki_match

    assert _hit_score({}) == 0.0
    day = (date.today() - timedelta(days=1)).isoformat()
    record = {"title": "FII selling drags Nifty lower", "published_at": f"{day}T10:00:00+00:00", "tags": {}}
    resolved = {
        "title": "FII selling drags Nifty lower",
        "publish_day": day,
        "headline": record,
    }
    assert score_wiki_match(record, {}, resolved, min_score=0.75) == 0.0


def test_resolve_hit_event_id_from_parquet_without_sidecar(hub_tmp):
    from trade_integrations.dataflows.hub_wiki.search_dedup import (
        build_source_event_index,
        resolve_hit_to_event_id,
    )

    day = (date.today() - timedelta(days=1)).isoformat()
    events_store.upsert_event(_similar_event("evt:parquet-only", publish_day=day))
    news_dir = hub_tmp / "llm-wiki" / "raw" / "sources" / "news"
    news_dir.mkdir(parents=True, exist_ok=True)
    index = build_source_event_index(news_dir=news_dir)
    hit = {"event_id": "evt:parquet-only", "score": 0.9}
    assert resolve_hit_to_event_id(hit, index) == "evt:parquet-only"


def test_build_duplicate_groups_wiki_includes_canonical_outside_lookback(hub_tmp, monkeypatch):
    from trade_integrations.dataflows.hub_wiki.search_dedup import build_duplicate_groups_wiki

    old_day = (date.today() - timedelta(days=120)).isoformat()
    recent_day = (date.today() - timedelta(days=1)).isoformat()
    canonical = _similar_event("evt:old-canonical", publish_day=recent_day)
    canonical.sources = [{"vendor": "rss", "url": "https://example.com/old", "publisher": "Mint"}]
    orphan = _similar_event("evt:recent-orphan", title_suffix=" dup", publish_day=recent_day)
    events_store.upsert_event(canonical)
    events_store.upsert_event(orphan)

    window = [events_store.distilled_event_to_headline_dict(events_store.get_event("evt:recent-orphan"))]

    def _fake_find(record, **kwargs):
        rid = str(record.get("canonical_story_id") or record.get("event_id") or "")
        if rid == "evt:recent-orphan":
            return {"event_id": "evt:old-canonical", "score": 0.9, "enrichment": {"references": []}}
        return None

    monkeypatch.setattr(
        "trade_integrations.dataflows.hub_wiki.search_dedup.find_wiki_match_for_record",
        _fake_find,
    )

    groups, stats = build_duplicate_groups_wiki(
        window,
        ticker="NIFTY",
        wiki_available=True,
    )
    assert stats["wiki_hits"] == 1
    assert len(groups) == 1
    members, target_id = groups[0]
    ids = {str(r.get("event_id") or r.get("canonical_story_id") or "") for r in members}
    assert ids == {"evt:old-canonical", "evt:recent-orphan"}
    assert target_id == "evt:old-canonical"


def test_wiki_merge_prefers_wiki_target_over_richer_orphan(hub_tmp, monkeypatch):
    monkeypatch.setenv("HUB_NEWS_RULE_FALLBACK_DISTILL", "1")
    monkeypatch.setenv("MINIMAX_API_KEY", "")
    monkeypatch.setenv("HUB_NEWS_DEDUP_SUMMARY_THRESHOLD", "0.72")
    from trade_integrations.dataflows.index_research import news_entity_worker as worker

    monkeypatch.setattr(worker, "require_minimax_for_distillation", lambda: None)

    day = (date.today() - timedelta(days=1)).isoformat()
    canonical = _similar_event("evt:wiki-target", publish_day=day)
    orphan = _similar_event("evt:rich-orphan", title_suffix=" dup", publish_day=day)
    orphan.sources = [
        {"vendor": "rss", "url": "https://example.com/a", "publisher": "A"},
        {"vendor": "rss", "url": "https://example.com/b", "publisher": "B"},
        {"vendor": "rss", "url": "https://example.com/c", "publisher": "C"},
    ]
    events_store.upsert_event(canonical)
    events_store.upsert_event(orphan)

    wiki_group = [
        events_store.distilled_event_to_headline_dict(events_store.get_event("evt:wiki-target")),
        events_store.distilled_event_to_headline_dict(events_store.get_event("evt:rich-orphan")),
    ]

    monkeypatch.setattr(
        "trade_integrations.dataflows.hub_wiki.search_dedup.wiki_search_available",
        lambda *a, **k: True,
    )
    monkeypatch.setattr(
        "trade_integrations.dataflows.hub_wiki.search_dedup.build_duplicate_groups_wiki",
        lambda *a, **k: ([(wiki_group, "evt:wiki-target")], {"wiki_search_queries": 1, "wiki_hits": 1}),
    )
    monkeypatch.setattr(
        "trade_integrations.dataflows.index_research.news_event_clubbing.build_duplicate_groups_two_signal",
        lambda *a, **k: [],
    )

    worker.compact_distilled_events(ticker="NIFTY", lookback_days=30, dry_run=False, max_passes=1)
    assert events_store.count_events(ticker="NIFTY") == 1
    assert events_store.get_event("evt:wiki-target") is not None
    assert events_store.get_event("evt:rich-orphan") is None


def test_score_wiki_match_rejects_cross_day(hub_tmp):
    from trade_integrations.dataflows.hub_wiki.search_dedup import score_wiki_match

    day_a = (date.today() - timedelta(days=1)).isoformat()
    day_b = (date.today() - timedelta(days=2)).isoformat()
    record = {"title": "FII selling drags Nifty lower", "published_at": f"{day_a}T10:00:00+00:00", "tags": {}}
    resolved = {"title": "FII selling drags Nifty lower", "publish_day": day_b}
    assert score_wiki_match(record, {"score": 0.95}, resolved) == 0.0


def test_part_had_errors_flags_sync_and_stage_failures():
    from trade_integrations.dataflows.index_research import news_entity_worker as worker

    assert worker._part_had_errors({"status": "error"}) is True
    assert worker._part_had_errors({"synced": False, "status": "error"}) is True
    assert worker._part_had_errors({"synced": False, "skipped": True}) is False
    assert worker._part_had_errors({"errors": 2}) is True
    assert worker._part_had_errors({"skipped": True}) is False


def test_run_hub_news_entity_job_paused_persists_maintenance_manifest(hub_tmp, monkeypatch):
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
    monkeypatch.setattr(
        "trade_integrations.hub_storage.news_migrations.ensure_hub_news_migrations",
        lambda **_: {"status": "ok"},
    )
    monkeypatch.setattr(
        "trade_integrations.dataflows.index_research.news_resolver.purge_stale_pending_refs",
        lambda **_: {"purged": 0},
    )

    def _should_not_run(*_a, **_k):
        raise AssertionError("maintenance stages must not run when pipeline is paused")

    monkeypatch.setattr(worker, "repair_leaked_distilled_summaries", _should_not_run)
    monkeypatch.setattr(worker, "backfill_distilled_event_metadata", _should_not_run)
    monkeypatch.setattr(worker, "compact_distilled_events", _should_not_run)

    result = worker.run_hub_news_entity_job({"ticker": "NIFTY", "mode": "maintenance"})
    assert result.get("pipeline_paused") is True
    assert result.get("fact_adjudication", {}).get("skipped") is True
    assert result.get("index_news_sync", {}).get("skipped") is True
    assert result.get("had_errors") is False

    manifest = worker.load_worker_last_summary()
    assert manifest is not None
    last = manifest.get("last_maintenance") or {}
    assert last.get("pipeline_paused") is True
    assert last.get("had_errors") is False
    stage_names = {s.get("stage") for s in last.get("stages") or []}
    assert "index_news_sync" in stage_names
    assert "wiki_backfill" in stage_names


def test_run_hub_news_entity_job_had_errors_when_index_sync_fails(monkeypatch):
    from trade_integrations.dataflows.index_research import news_entity_worker as worker

    monkeypatch.setattr(
        worker,
        "process_staging_batch",
        lambda **_: {"processed": 0, "created": 0, "updated": 0},
    )
    monkeypatch.setattr(
        "trade_integrations.hub_storage.news_staging_store.pipeline_pause_status",
        lambda **_: {"pipeline_paused": False, "pause_reason": "", "pending": {"queued": 0}},
    )
    monkeypatch.setattr(
        "trade_integrations.hub_storage.news_migrations.ensure_hub_news_migrations",
        lambda **_: {"status": "ok"},
    )
    monkeypatch.setattr(worker, "_tickers_with_pending_staging", lambda: [])
    monkeypatch.setattr(
        "trade_integrations.dataflows.index_research.news_resolver.purge_stale_pending_refs",
        lambda **_: {"purged": 0},
    )
    monkeypatch.setattr(worker, "repair_leaked_distilled_summaries", lambda **_: {"repaired": 0})
    monkeypatch.setattr(worker, "backfill_distilled_event_metadata", lambda **_: {"backfilled": 0})
    monkeypatch.setattr(
        worker,
        "_finalize_legacy_ssot_if_ready",
        lambda **_: {"skipped": True, "reason": "already_finalized"},
    )
    monkeypatch.setattr(
        "trade_integrations.dataflows.index_research.news_maintainer_facts.run_fact_adjudication_backfill",
        lambda **_: {"events_scanned": 0, "errors": 0},
    )
    monkeypatch.setattr(worker, "compact_distilled_events", lambda **_: {"groups_merged": 0, "rows_removed": 0})
    monkeypatch.setattr(
        "trade_integrations.dataflows.index_research.news_maintainer_safety_sweep.run_maintenance_safety_sweep",
        lambda **_: {"groups_merged": 0, "rows_removed": 0},
    )
    monkeypatch.setattr(
        "trade_integrations.dataflows.index_research.news_cleanup.cleanup_hub_news",
        lambda **_: {"removed": 0},
    )
    monkeypatch.setattr(
        "trade_integrations.dataflows.index_research.news_rollup.rollup_parent_topic_events",
        lambda **_: {"rolled_up": 0},
    )
    monkeypatch.setattr(
        worker,
        "_refresh_news_impact_cache",
        lambda **_: {"status": "ok", "ticker": "NIFTY"},
    )
    monkeypatch.setattr(
        worker,
        "_sync_index_news_after_maintenance",
        lambda **_: {"synced": False, "status": "error", "reason": "no_index_doc"},
    )

    result = worker.run_hub_news_entity_job({"ticker": "NIFTY", "mode": "maintenance"})
    assert result.get("index_news_sync", {}).get("synced") is False
    assert result.get("had_errors") is True
    manifest = worker.load_worker_last_summary()
    assert (manifest or {}).get("last_maintenance", {}).get("had_errors") is True


def test_part_had_errors_flags_wiki_backfill_list_errors():
    from trade_integrations.dataflows.index_research import news_entity_worker as worker

    assert worker._part_had_errors({"ok": False, "errors": ["evt:a: boom"]}) is True
    assert worker._part_had_errors({"ok": False, "skipped": True}) is False


def test_run_hub_news_entity_job_drain_had_errors_when_staging_ttl_fails(monkeypatch):
    from trade_integrations.dataflows.index_research import news_entity_worker as worker

    monkeypatch.setattr(
        worker,
        "process_staging_batch",
        lambda **_: {"processed": 1, "created": 0, "updated": 1},
    )
    monkeypatch.setattr(
        "trade_integrations.hub_storage.news_staging_store.pipeline_pause_status",
        lambda **_: {"pipeline_paused": False, "pause_reason": "", "pending": {"queued": 0}},
    )
    monkeypatch.setattr(
        "trade_integrations.hub_storage.news_migrations.ensure_hub_news_migrations",
        lambda **_: {"status": "ok"},
    )
    monkeypatch.setattr(worker, "_tickers_with_pending_staging", lambda: [])
    monkeypatch.setattr(worker, "compact_distilled_events", lambda **_: {"groups_merged": 0})
    monkeypatch.setattr(
        "trade_integrations.dataflows.index_research.news_resolver.purge_stale_pending_refs",
        lambda **_: {"status": "error", "error": "ttl failed"},
    )

    result = worker.run_hub_news_entity_job({"ticker": "NIFTY", "mode": "drain"})
    assert result.get("staging_ttl_purge", {}).get("status") == "error"
    assert result.get("had_errors") is True


def test_run_hub_news_entity_job_had_errors_when_wiki_backfill_partial_fail(monkeypatch):
    from trade_integrations.dataflows.index_research import news_entity_worker as worker

    monkeypatch.setattr(
        worker,
        "process_staging_batch",
        lambda **_: {"processed": 0, "created": 0, "updated": 0},
    )
    monkeypatch.setattr(
        "trade_integrations.hub_storage.news_staging_store.pipeline_pause_status",
        lambda **_: {"pipeline_paused": False, "pause_reason": "", "pending": {"queued": 0}},
    )
    monkeypatch.setattr(
        "trade_integrations.hub_storage.news_migrations.ensure_hub_news_migrations",
        lambda **_: {"status": "ok"},
    )
    monkeypatch.setattr(worker, "_tickers_with_pending_staging", lambda: [])
    monkeypatch.setattr(
        "trade_integrations.dataflows.index_research.news_resolver.purge_stale_pending_refs",
        lambda **_: {"purged": 0},
    )
    monkeypatch.setattr(worker, "repair_leaked_distilled_summaries", lambda **_: {"repaired": 0})
    monkeypatch.setattr(worker, "backfill_distilled_event_metadata", lambda **_: {"backfilled": 0})
    monkeypatch.setattr(
        worker,
        "_finalize_legacy_ssot_if_ready",
        lambda **_: {"skipped": True, "reason": "already_finalized"},
    )
    monkeypatch.setattr(
        "trade_integrations.dataflows.index_research.news_maintainer_facts.run_fact_adjudication_backfill",
        lambda **_: {"events_scanned": 0, "errors": 0},
    )
    monkeypatch.setattr(worker, "compact_distilled_events", lambda **_: {"groups_merged": 0, "rows_removed": 0})
    monkeypatch.setattr(
        "trade_integrations.dataflows.index_research.news_maintainer_safety_sweep.run_maintenance_safety_sweep",
        lambda **_: {"groups_merged": 0, "rows_removed": 0},
    )
    monkeypatch.setattr(
        "trade_integrations.dataflows.index_research.news_cleanup.cleanup_hub_news",
        lambda **_: {"removed": 0},
    )
    monkeypatch.setattr(
        "trade_integrations.dataflows.index_research.news_rollup.rollup_parent_topic_events",
        lambda **_: {"rolled_up": 0},
    )
    monkeypatch.setattr(
        worker,
        "_refresh_news_impact_cache",
        lambda **_: {"status": "ok", "ticker": "NIFTY"},
    )
    monkeypatch.setattr(
        worker,
        "_sync_index_news_after_maintenance",
        lambda **_: {"synced": True, "ticker": "NIFTY"},
    )
    monkeypatch.setattr(
        "trade_integrations.dataflows.hub_wiki.compile.wiki_backfill_enabled",
        lambda: True,
    )
    monkeypatch.setattr(
        "trade_integrations.dataflows.hub_wiki.compile.compile_all_events_to_wiki",
        lambda **_: {"ok": False, "errors": ["evt:x: compile failed"], "compiled": 0},
    )

    result = worker.run_hub_news_entity_job({"ticker": "NIFTY", "mode": "maintenance"})
    assert result.get("wiki_backfill", {}).get("ok") is False
    assert result.get("had_errors") is True


def test_sync_index_news_reuses_impact_refresh_without_second_build(monkeypatch):
    from trade_integrations.dataflows.index_research import news_entity_worker as worker

    calls: list[str] = []

    def _refresh(**_):
        calls.append("refresh")
        return {"status": "ok", "ticker": "NIFTY"}

    monkeypatch.setattr(worker, "_refresh_news_impact_cache", _refresh)
    monkeypatch.setattr(
        "trade_integrations.context.hub.load_index_research_json",
        lambda _sym: None,
    )

    impact = {"status": "ok", "ticker": "NIFTY", "from": "test"}
    worker._sync_index_news_after_maintenance(ticker="NIFTY", impact_refresh=impact)
    assert calls == []


def test_merge_staging_summaries_propagates_stage_errors():
    from trade_integrations.dataflows.index_research import news_entity_worker as worker

    merged = worker._merge_staging_summaries(
        [
            {"status": "error", "stage": "staging", "error": "boom"},
            {"processed": 5, "created": 0, "updated": 5, "errors": 0, "ticker": "BANKNIFTY"},
        ]
    )
    assert merged.get("status") == "error"
    assert worker._part_had_errors(merged) is True
