"""Tests for LLM-Wiki probe and hub news ingest gate."""

from __future__ import annotations

import pytest


@pytest.fixture
def hub_tmp(tmp_path, monkeypatch):
    from trade_integrations.context import hub as hub_mod

    hub = tmp_path / "hub"
    hub.mkdir()
    monkeypatch.setattr(hub_mod, "get_hub_dir", lambda: hub)
    return hub


def test_llm_wiki_required_respects_env(monkeypatch):
    from trade_integrations.dataflows.hub_wiki.probe import llm_wiki_required_for_hub_news

    monkeypatch.setenv("HUB_NEWS_REQUIRE_LLM_WIKI", "0")
    assert llm_wiki_required_for_hub_news() is False

    monkeypatch.setenv("HUB_NEWS_REQUIRE_LLM_WIKI", "1")
    monkeypatch.setattr(
        "trade_integrations.hub_storage.news_staging_store.is_entity_pipeline_enabled",
        lambda: True,
    )
    assert llm_wiki_required_for_hub_news() is True


def test_ingest_blocked_when_probe_fails(monkeypatch):
    from trade_integrations.dataflows.hub_wiki.probe import check_ingest_allowed, ingest_blocked_by_wiki

    monkeypatch.setenv("HUB_NEWS_REQUIRE_LLM_WIKI", "1")
    monkeypatch.setattr(
        "trade_integrations.hub_storage.news_staging_store.is_entity_pipeline_enabled",
        lambda: True,
    )
    monkeypatch.setattr(
        "trade_integrations.dataflows.hub_wiki.probe.probe_llm_wiki",
        lambda **_: {"ok": False, "reason": "health_check_failed"},
    )

    block = ingest_blocked_by_wiki()
    assert block is not None
    assert block.get("blocked") is True
    assert block.get("reason") == "llm_wiki_unavailable"

    gate = check_ingest_allowed()
    assert gate.get("blocked") is True


def test_ingest_allowed_when_require_disabled(monkeypatch):
    from trade_integrations.dataflows.hub_wiki.probe import check_ingest_allowed

    monkeypatch.setenv("HUB_NEWS_REQUIRE_LLM_WIKI", "0")
    gate = check_ingest_allowed()
    assert gate.get("blocked") is False


def test_ingest_rows_blocked_when_wiki_down(hub_tmp, monkeypatch):
    from trade_integrations.context import hub as hub_mod
    from trade_integrations.dataflows.news_hub_bridge._ingest import ingest_rows_to_hub
    from trade_integrations.hub_storage import news_staging_store as staging_store

    monkeypatch.setattr(hub_mod, "get_hub_dir", lambda: hub_tmp)
    monkeypatch.setattr(staging_store, "get_hub_dir", lambda: hub_tmp)
    monkeypatch.setattr(staging_store, "is_entity_pipeline_enabled", lambda: True)
    monkeypatch.setattr(staging_store, "is_legacy_ingest_enabled", lambda: False)
    monkeypatch.setattr(
        "trade_integrations.dataflows.hub_wiki.probe.check_ingest_allowed",
        lambda **_: {
            "blocked": True,
            "reason": "llm_wiki_unavailable",
            "user_message": "Start LLM Wiki.app",
        },
    )

    stats = ingest_rows_to_hub(
        [{"title": "Headline", "url": "https://example.com/a", "published_at": "2026-07-16"}],
        ticker="NIFTY",
    )
    assert stats.get("blocked") is True
    assert stats.get("queued", stats.get("ingested", 1)) == 0
    assert staging_store.list_pending_refs(ticker="NIFTY", limit=5) == []


def test_pipeline_pause_status_wiki_first(hub_tmp, monkeypatch):
    from trade_integrations.hub_storage import news_staging_store as staging_store

    monkeypatch.setattr(staging_store, "get_hub_dir", lambda: hub_tmp)
    monkeypatch.setattr(staging_store, "is_entity_pipeline_enabled", lambda: True)
    monkeypatch.setattr(staging_store, "minimax_configured", lambda: True)
    monkeypatch.setattr(
        "trade_integrations.dataflows.hub_wiki.probe.ingest_blocked_by_wiki",
        lambda **_: {
            "blocked": True,
            "reason": "llm_wiki_unavailable",
            "user_message": "Start LLM Wiki.app",
            "llm_wiki": {"ok": False},
        },
    )

    pause = staging_store.pipeline_pause_status(ticker="NIFTY")
    assert pause.get("pipeline_paused") is True
    assert pause.get("pause_reason") == "llm_wiki_unavailable"
    assert pause.get("user_message") == "Start LLM Wiki.app"
    assert pause.get("llm_wiki_ok") is False


def test_build_hub_status_forwards_wiki_fields(hub_tmp, monkeypatch):
    from trade_integrations.hub_storage.hub_status import build_hub_status

    monkeypatch.setattr(
        "trade_integrations.hub_storage.hub_status.pipeline_pause_status",
        lambda **_: {
            "pipeline_paused": True,
            "pause_reason": "llm_wiki_unavailable",
            "user_message": "Start LLM Wiki.app",
            "llm_wiki_ok": False,
            "llm_wiki_required": True,
            "minimax_configured": True,
            "pending": {"queued": 0},
        },
    )
    monkeypatch.setattr(
        "trade_integrations.dataflows.news_hub_bridge.hub_news_pipeline_status",
        lambda **_: {"pipeline_paused": True},
    )

    status = build_hub_status(entity_id="NIFTY")
    staging = status.get("news_staging") or {}
    assert staging.get("user_message") == "Start LLM Wiki.app"
    assert staging.get("llm_wiki_ok") is False
    assert staging.get("llm_wiki_required") is True
