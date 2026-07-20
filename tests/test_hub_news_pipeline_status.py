"""Tests for hub news pipeline status and LLM-Wiki bootstrap."""

from __future__ import annotations

import pytest


@pytest.fixture
def hub_tmp(tmp_path, monkeypatch):
    from trade_integrations.context import hub as hub_mod

    hub = tmp_path / "hub"
    hub.mkdir()
    monkeypatch.setattr(hub_mod, "get_hub_dir", lambda: hub)
    return hub


def test_staging_queue_detail_oldest_age(hub_tmp, monkeypatch):
    from trade_integrations.hub_storage import news_staging_store as staging_store

    monkeypatch.setattr(staging_store, "get_hub_dir", lambda: hub_tmp)
    staging_store.enqueue_raw_ref(
        {
            "title": "Old headline",
            "summary": "Body",
            "url": "https://example.com/old",
            "published_at": "2026-07-16",
        },
        ticker="NIFTY",
    )
    detail = staging_store.staging_queue_detail(ticker="NIFTY")
    assert detail["queued"] == 1
    assert detail["oldest_pending_seconds"] >= 0


def test_legacy_ingest_skips_staging(hub_tmp, monkeypatch):
    from trade_integrations.context import hub as hub_mod
    from trade_integrations.dataflows.news_hub_bridge._ingest import ingest_rows_to_hub
    from trade_integrations.dataflows.index_research import news_impact_engine as engine
    from trade_integrations.hub_storage import news_staging_store as staging_store

    monkeypatch.setattr(hub_mod, "get_hub_dir", lambda: hub_tmp)
    monkeypatch.setattr(staging_store, "get_hub_dir", lambda: hub_tmp)
    monkeypatch.setattr(engine, "get_hub_dir", lambda: hub_tmp)
    monkeypatch.setattr(staging_store, "is_entity_pipeline_enabled", lambda: True)
    monkeypatch.setattr(staging_store, "is_legacy_ingest_enabled", lambda: True)
    monkeypatch.setattr(
        engine,
        "load_aligned_factor_history",
        lambda **_: __import__("pandas").DataFrame(
            {"date": ["2026-07-16"], "close": [25000.0], "fii_net_5d": [-1000.0]}
        ),
    )
    monkeypatch.setattr(engine, "verify_enriched_news", lambda *a, **k: __import__(
        "trade_integrations.dataflows.index_research.news_verification",
        fromlist=["VerifiedClaim", "_approval_from_claims"],
    )._approval_from_claims([]))

    stats = ingest_rows_to_hub(
        [
            {
                "title": "Legacy path headline",
                "summary": "Direct verify",
                "url": "https://example.com/legacy",
                "published_at": "2026-07-16",
            }
        ],
        ticker="NIFTY",
    )
    assert stats.get("verified", 0) >= 1 or stats.get("ingested", 0) >= 1
    assert staging_store.list_pending_refs(ticker="NIFTY", limit=5) == []


def test_hub_news_pipeline_status(hub_tmp, monkeypatch):
    from trade_integrations.dataflows.news_hub_bridge import hub_news_pipeline_status

    monkeypatch.setattr(
        "trade_integrations.dataflows.hub_wiki.client.health_check",
        lambda: {"ok": True, "status": "running", "version": "0.6.4"},
    )

    status = hub_news_pipeline_status(ticker="NIFTY")
    assert status["ssot"] == "events.parquet"
    assert "staging" in status
    assert status["llm_wiki"]["project_dir"].endswith("llm-wiki")
    assert status["llm_wiki"]["health"]["ok"] is True


def test_ensure_llm_wiki_project_creates_tree(hub_tmp):
    from trade_integrations.dataflows.hub_wiki import ensure_llm_wiki_project, get_llm_wiki_project_dir

    root = ensure_llm_wiki_project()
    assert root == get_llm_wiki_project_dir()
    assert (root / "wiki" / "index.md").is_file()
    assert (root / "schema.md").is_file()
    assert (root / "sources" / "inbox").is_dir()
    assert (root / "wiki" / "events").is_dir()
