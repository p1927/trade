"""Tests for hub wiki audit and backfill."""

from __future__ import annotations

import json

import pytest

from tests.conftest import patch_hub_wiki_dirs


@pytest.fixture
def hub_tmp(tmp_path, monkeypatch):
    from trade_integrations.context import hub as hub_mod

    hub = tmp_path / "hub"
    hub.mkdir()
    patch_hub_wiki_dirs(monkeypatch, hub)
    monkeypatch.setattr(hub_mod, "get_hub_dir", lambda: hub)
    return hub


def test_audit_reports_missing_exports(hub_tmp, monkeypatch):
    from trade_integrations.dataflows.hub_wiki.audit import audit_hub_wiki_sync
    from trade_integrations.hub_storage.news_events_store import upsert_event
    from trade_integrations.hub_storage.news_event_models import DistilledNewsEvent

    upsert_event(
        DistilledNewsEvent(
            event_id="evt:audit1",
            ticker="NIFTY",
            title="RBI holds rates",
            content="Policy unchanged.",
        )
    )
    monkeypatch.setattr(
        "trade_integrations.dataflows.hub_wiki.audit.probe_llm_wiki",
        lambda **_: {"ok": True},
    )

    report = audit_hub_wiki_sync(ticker="NIFTY")
    assert report["events_active"] == 1
    assert report["missing_export"] == ["evt:audit1"]
    assert report["coverage_pct"] == 0.0


def test_audit_reports_covered_after_compile(hub_tmp, monkeypatch):
    from trade_integrations.dataflows.hub_wiki.audit import audit_hub_wiki_sync
    from trade_integrations.dataflows.hub_wiki.compile import compile_event_to_wiki
    from trade_integrations.hub_storage.news_events_store import upsert_event
    from trade_integrations.hub_storage.news_event_models import DistilledNewsEvent

    upsert_event(
        DistilledNewsEvent(
            event_id="evt:covered1",
            ticker="NIFTY",
            title="Oil rises on supply fears",
            content="Brent climbed 2%.",
            processing_version=2,
        )
    )
    compile_event_to_wiki(
        {
            "event_id": "evt:covered1",
            "ticker": "NIFTY",
            "title": "Oil rises on supply fears",
            "content": "Brent climbed 2%.",
            "processing_version": 2,
        },
        rescan=False,
    )
    monkeypatch.setattr(
        "trade_integrations.dataflows.hub_wiki.audit.probe_llm_wiki",
        lambda **_: {"ok": True},
    )

    report = audit_hub_wiki_sync(ticker="NIFTY")
    assert report["events_active"] == 1
    assert report["covered"] == 1
    assert report["missing_export"] == []
    assert report["stale_export"] == []
    assert report["coverage_pct"] == 100.0


def test_compile_all_events_to_wiki_backfill(hub_tmp, monkeypatch):
    from trade_integrations.dataflows.hub_wiki.compile import compile_all_events_to_wiki
    from trade_integrations.hub_storage.news_events_store import upsert_event
    from trade_integrations.hub_storage.news_event_models import DistilledNewsEvent

    upsert_event(
        DistilledNewsEvent(
            event_id="evt:backfill1",
            ticker="NIFTY",
            title="FII selling pressure",
            content="Outflows continue.",
        )
    )
    monkeypatch.setattr(
        "trade_integrations.dataflows.hub_wiki.compile.batch_rescan_if_enabled",
        lambda **_: {"ok": True, "skipped": True},
    )

    result = compile_all_events_to_wiki(ticker="NIFTY", rescan=True)
    assert result["events_active"] == 1
    assert result["compiled"] == 1
    news_dir = hub_tmp / "llm-wiki" / "raw" / "sources" / "news"
    assert any(news_dir.glob("*.md"))
    assert not any(news_dir.glob("*.json"))
    from trade_integrations.dataflows.hub_wiki.frontmatter import read_frontmatter

    md_path = next(news_dir.glob("*.md"))
    fm = read_frontmatter(md_path)
    assert fm.get("event_id") == "evt:backfill1"
    assert fm.get("content_fingerprint")

    second = compile_all_events_to_wiki(ticker="NIFTY", rescan=False)
    assert second["skipped_current"] == 1
    assert second["compiled"] == 0


def test_audit_flags_stale_export_without_fingerprint(hub_tmp, monkeypatch):
    from trade_integrations.dataflows.hub_wiki.audit import audit_hub_wiki_sync
    from trade_integrations.dataflows.hub_wiki.compile import event_slug
    from trade_integrations.hub_storage.news_events_store import upsert_event
    from trade_integrations.hub_storage.news_event_models import DistilledNewsEvent

    event = DistilledNewsEvent(
        event_id="evt:legacy1",
        ticker="NIFTY",
        title="Legacy export",
        content="Old export without fingerprint.",
    )
    upsert_event(event)
    news_dir = hub_tmp / "llm-wiki" / "raw" / "sources" / "news"
    news_dir.mkdir(parents=True, exist_ok=True)
    slug = event_slug(event.to_dict())
    (news_dir / f"{slug}.md").write_text(
        "---\nevent_id: evt:legacy1\ntitle: Legacy export\n---\n# legacy\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "trade_integrations.dataflows.hub_wiki.audit.probe_llm_wiki",
        lambda **_: {"ok": True},
    )

    report = audit_hub_wiki_sync(ticker="NIFTY")
    assert report["stale_export"] == ["evt:legacy1"]
    assert report["covered"] == 0
    assert report["json_sidecars_remaining"] == 0


def test_event_slug_preserves_unique_suffix():
    from trade_integrations.dataflows.hub_wiki.compile import event_slug

    long_title = "Indian Stock Markets Poised to Extend Rally on Positive Global Cues Today"
    a = event_slug({"event_id": "aa250ffb-b82a-41df-acbc-2e93b77008d3", "title": long_title})
    b = event_slug({"event_id": "15fbed22-31f4-4ab3-978e-fee598b7d2ad", "title": long_title})
    assert a != b
    assert "2e93b77008d3" in a
    assert "fee598b7d2ad" in b


def test_bootstrap_creates_native_wiki_dirs(hub_tmp, monkeypatch):
    from trade_integrations.dataflows.hub_wiki import ensure_llm_wiki_project

    root = ensure_llm_wiki_project()
    assert (root / "raw" / "sources" / "research").is_dir()
    assert (root / "wiki" / "entities").is_dir()
    assert (root / "wiki" / "concepts").is_dir()
    assert (root / "wiki" / "queries").is_dir()


def test_cleanup_legacy_wiki_artifacts(hub_tmp, monkeypatch):
    from trade_integrations.dataflows.hub_wiki.bootstrap import (
        cleanup_legacy_wiki_artifacts,
        legacy_wiki_layout_report,
    )

    legacy_events = hub_tmp / "llm-wiki" / "wiki" / "events"
    legacy_events.mkdir(parents=True)
    (legacy_events / "old-event.md").write_text("# legacy\n", encoding="utf-8")
    legacy_src = hub_tmp / "llm-wiki" / "sources" / "news"
    legacy_src.mkdir(parents=True)
    (legacy_src / "stale.md").write_text("# stale\n", encoding="utf-8")

    before = legacy_wiki_layout_report()
    assert before["legacy_wiki_events_files"] == 1
    assert before["legacy_sources_files"] == 1

    result = cleanup_legacy_wiki_artifacts(dry_run=False)
    assert result["removed_files"] == 2

    after = legacy_wiki_layout_report()
    assert after["legacy_wiki_events_files"] == 0
    assert after["legacy_sources_files"] == 0


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
