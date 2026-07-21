"""Tests for hub news pipeline schedule config."""

from __future__ import annotations

import json
import pytest


@pytest.fixture
def hub_tmp(tmp_path, monkeypatch):
    from trade_integrations.context import hub as hub_mod

    hub = tmp_path / "hub"
    hub.mkdir()
    monkeypatch.setattr(hub_mod, "get_hub_dir", lambda: hub)
    monkeypatch.setattr(
        "trade_integrations.hub_storage.news_pipeline_config.get_hub_dir",
        lambda: hub,
    )
    return hub


def test_env_defaults_full_and_light(hub_tmp, monkeypatch):
    monkeypatch.setenv("HUB_NEWS_FULL_INGEST_CRON", "0 6 * * *")
    monkeypatch.setenv("HUB_NEWS_LIGHT_INGEST_CRON", "0 */3 * * *")
    monkeypatch.setenv("HUB_NEWS_LIGHT_SOURCES", "rss")

    from trade_integrations.hub_storage.news_pipeline_config import env_defaults

    cfg = env_defaults()
    assert cfg.full_ingest_cron == "0 6 * * *"
    assert cfg.light_ingest_cron == "0 */3 * * *"
    assert cfg.light_ingest_sources == "rss"


def test_persist_and_merge_override(hub_tmp):
    from trade_integrations.hub_storage.news_pipeline_config import (
        load_news_pipeline_config,
        update_news_pipeline_config,
    )

    update_news_pipeline_config({"light_ingest_cron": "15 */2 * * *", "light_ingest_enabled": False})
    cfg = load_news_pipeline_config()
    assert cfg.light_ingest_cron == "15 */2 * * *"
    assert cfg.light_ingest_enabled is False
    assert cfg.full_ingest_cron  # still from env default

    path = hub_tmp / "_data" / "news_pipeline" / "config.json"
    assert path.is_file()
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved["light_ingest_enabled"] is False


def test_config_for_api_includes_modes(hub_tmp):
    from trade_integrations.hub_storage.news_pipeline_config import config_for_api

    payload = config_for_api()
    assert payload["ingest_modes"]["full"]["cron"]
    assert "light" in payload["ingest_modes"]
    assert payload["job_ids"]["full_ingest"] == "nifty-hub-news-ingest-full"


def test_run_hub_news_ingest_light_mode_sources(hub_tmp, monkeypatch):
    from trade_integrations.hub_storage import news_staging_store as staging_store
    from trade_integrations.hub_storage.news_pipeline_config import update_news_pipeline_config

    monkeypatch.setattr(staging_store, "get_hub_dir", lambda: hub_tmp)
    update_news_pipeline_config({"light_ingest_sources": "rss"})

    called: dict[str, str] = {}

    def fake_rss(**kwargs):
        called["rss"] = kwargs.get("ticker", "")
        return {"queued": 1, "ingested": 1}

    monkeypatch.setattr(
        "trade_integrations.dataflows.index_research.hub_news_ingest._ingest_rss",
        fake_rss,
    )

    from trade_integrations.dataflows.index_research.hub_news_ingest import run_hub_news_ingest

    result = run_hub_news_ingest(ticker="NIFTY", mode="light", sources="default")
    assert result["mode"] == "light"
    assert result["sync_distill_limit"] == 0
    assert "market_context" in result
    assert "rss" in result["sources_requested"]
    assert "watcher" not in result["sources_requested"]
    assert called.get("rss") == "NIFTY"


def test_wiki_search_config_defaults(hub_tmp, monkeypatch):
    monkeypatch.setenv("HUB_NEWS_WIKI_SEARCH_ENABLED", "1")
    monkeypatch.setenv("HUB_NEWS_WIKI_SEARCH_TOP_K", "7")
    monkeypatch.setenv("HUB_NEWS_WIKI_SEARCH_MAX_PER_PASS", "99")
    monkeypatch.setenv("HUB_NEWS_WIKI_SEARCH_MIN_SCORE", "0.8")

    from trade_integrations.hub_storage.news_pipeline_config import env_defaults

    cfg = env_defaults()
    assert cfg.wiki_search_enabled is True
    assert cfg.wiki_search_top_k == 7
    assert cfg.wiki_search_max_per_pass == 99
    assert cfg.wiki_search_min_score == 0.8


def test_wiki_search_config_persist_and_api(hub_tmp):
    from trade_integrations.hub_storage.news_pipeline_config import (
        config_for_api,
        load_news_pipeline_config,
        update_news_pipeline_config,
    )

    update_news_pipeline_config(
        {
            "wiki_search_enabled": False,
            "wiki_search_top_k": 3,
            "wiki_search_max_per_pass": 40,
            "wiki_search_min_score": 0.82,
        }
    )
    cfg = load_news_pipeline_config()
    assert cfg.wiki_search_enabled is False
    assert cfg.wiki_search_top_k == 3
    assert cfg.wiki_search_max_per_pass == 40
    assert cfg.wiki_search_min_score == 0.82

    payload = config_for_api()
    assert payload["wiki_search_enabled"] is False
    assert payload["wiki_search_top_k"] == 3
    assert payload["wiki_search_max_per_pass"] == 40
    assert payload["wiki_search_min_score"] == 0.82
