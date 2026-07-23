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
    sidecar = next(news_dir.glob("*.json"))
    payload = json.loads(sidecar.read_text(encoding="utf-8"))
    assert payload["event_id"] == "evt:backfill1"
    assert payload.get("content_fingerprint")

    second = compile_all_events_to_wiki(ticker="NIFTY", rescan=False)
    assert second["skipped_current"] == 1
    assert second["compiled"] == 0


def test_audit_flags_legacy_export_without_fingerprint(hub_tmp, monkeypatch):
    from trade_integrations.dataflows.hub_wiki.audit import audit_hub_wiki_sync
    from trade_integrations.dataflows.hub_wiki.compile import event_slug
    from trade_integrations.hub_storage.news_events_store import upsert_event
    from trade_integrations.hub_storage.news_event_models import DistilledNewsEvent

    event = DistilledNewsEvent(
        event_id="evt:legacy1",
        ticker="NIFTY",
        title="Legacy export",
        content="Old sidecar without fingerprint.",
    )
    upsert_event(event)
    news_dir = hub_tmp / "llm-wiki" / "raw" / "sources" / "news"
    news_dir.mkdir(parents=True, exist_ok=True)
    slug = event_slug(event.to_dict())
    (news_dir / f"{slug}.md").write_text("# legacy\n", encoding="utf-8")
    (news_dir / f"{slug}.json").write_text(
        json.dumps({"event_id": "evt:legacy1", "title": "Legacy export"}),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "trade_integrations.dataflows.hub_wiki.audit.probe_llm_wiki",
        lambda **_: {"ok": True},
    )

    report = audit_hub_wiki_sync(ticker="NIFTY")
    assert report["stale_export"] == ["evt:legacy1"]
    assert report["covered"] == 0


def test_bootstrap_creates_native_wiki_dirs(hub_tmp, monkeypatch):
    from trade_integrations.dataflows.hub_wiki import ensure_llm_wiki_project

    root = ensure_llm_wiki_project()
    assert (root / "raw" / "sources" / "research").is_dir()
    assert (root / "wiki" / "entities").is_dir()
    assert (root / "wiki" / "concepts").is_dir()
    assert (root / "wiki" / "queries").is_dir()
