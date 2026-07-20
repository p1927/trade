"""Tests for news relevance gate and discarded ledger."""

from __future__ import annotations

import pytest


@pytest.fixture
def hub_tmp(tmp_path, monkeypatch):
    from trade_integrations.context import hub as hub_mod

    hub = tmp_path / "hub"
    hub.mkdir()
    monkeypatch.setattr(hub_mod, "get_hub_dir", lambda: hub)
    return hub


def test_rule_prefilter_junk(hub_tmp):
    from trade_integrations.dataflows.index_research.news_relevance import rule_prefilter

    verdict = rule_prefilter({"title": "IPL cricket final score", "summary": "Team wins trophy"})
    assert verdict is not None
    assert verdict.relevant is False
    assert verdict.confidence >= 0.8


def test_rule_prefilter_market(hub_tmp):
    from trade_integrations.dataflows.index_research.news_relevance import rule_prefilter

    verdict = rule_prefilter(
        {
            "title": "FII sell-off drags Nifty lower",
            "summary": "Foreign investors pulled out Rs 2000 crore",
        }
    )
    assert verdict is not None
    assert verdict.relevant is True


def test_discard_ledger_round_trip(hub_tmp, monkeypatch):
    from trade_integrations.hub_storage import news_staging_store as staging

    monkeypatch.setattr(staging, "get_hub_dir", lambda: hub_tmp)
    ref_id, _ = staging.enqueue_raw_ref(
        {
            "title": "Celebrity wedding photos",
            "summary": "Bollywood star",
            "url": "https://example.com/celebrity",
        },
        ticker="NIFTY",
    )
    row = staging.mark_ref_discarded(
        ref_id,
        reason="test discard",
        relevance={"relevant": False, "confidence": 0.9},
        source_kind="auto_gate",
    )
    assert row is not None
    assert staging.list_pending_refs(ticker="NIFTY") == []
    discarded = staging.list_discarded_refs(ticker="NIFTY")
    assert len(discarded) == 1
    assert discarded[0]["discard_id"]

    restored = staging.restore_discarded(discarded[0]["discard_id"])
    assert restored["restored"] is True
    pending = staging.list_pending_refs(ticker="NIFTY")
    assert len(pending) == 1


def test_process_staging_ref_discards_irrelevant(hub_tmp, monkeypatch):
    from trade_integrations.dataflows.index_research import news_entity_worker as worker
    from trade_integrations.hub_storage import news_staging_store as staging

    monkeypatch.setattr(staging, "get_hub_dir", lambda: hub_tmp)
    monkeypatch.setattr(worker, "is_entity_pipeline_enabled", lambda: True)
    monkeypatch.setattr(
        "trade_integrations.dataflows.index_research.news_relevance.assess_ref_relevance",
        lambda ref, ticker="NIFTY": __import__(
            "trade_integrations.dataflows.index_research.news_relevance",
            fromlist=["RelevanceVerdict"],
        ).RelevanceVerdict(relevant=False, confidence=0.95, reason="test"),
    )

    ref_id, _ = staging.enqueue_raw_ref(
        {
            "title": "Random sports",
            "summary": "Local football league",
            "url": "https://example.com/sports",
        },
        ticker="NIFTY",
    )
    ref = staging.list_pending_refs(ticker="NIFTY")[0]
    result = worker.process_staging_ref(ref, ticker="NIFTY")
    assert result.get("action") == "discard_irrelevant"
    assert staging.discarded_count(ticker="NIFTY") == 1
