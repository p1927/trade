"""Tests for unified hub news staging resolver (T0–T3)."""

from __future__ import annotations

import pytest


@pytest.fixture
def hub_tmp(tmp_path, monkeypatch):
    from trade_integrations.context import hub as hub_mod

    hub = tmp_path / "hub"
    hub.mkdir()
    monkeypatch.setattr(hub_mod, "get_hub_dir", lambda: hub)
    return hub


def test_resolve_discards_irrelevant(hub_tmp, monkeypatch):
    from trade_integrations.dataflows.index_research import news_resolver as resolver

    monkeypatch.setattr(
        resolver,
        "assess_ref_relevance",
        lambda *a, **k: type(
            "V",
            (),
            {
                "relevant": False,
                "confidence": 0.9,
                "reason": "junk",
                "to_dict": lambda self: {"relevant": False},
            },
        )(),
    )
    monkeypatch.setattr(resolver, "relevance_min_confidence", lambda: 0.6)
    monkeypatch.setattr(resolver, "mark_ref_discarded", lambda *a, **k: None)

    decision = resolver.resolve_staging_group(
        [{"ref_id": "ref:1", "title": "Celebrity gossip", "url": "https://example.com/a"}],
        ticker="NIFTY",
    )
    assert decision.action == "discard"
    assert decision.reason == "all_refs_irrelevant"
    assert decision.tier == "t0"


def test_resolve_syndicated_match_discards(hub_tmp, monkeypatch):
    from trade_integrations.dataflows.index_research import news_resolver as resolver

    monkeypatch.setattr(resolver, "filter_relevant_refs", lambda refs, **k: (refs, []))
    monkeypatch.setattr(
        "trade_integrations.dataflows.index_research.news_claim_extraction.enrich_ref_with_claims",
        lambda r: dict(r),
    )
    monkeypatch.setattr(
        "trade_integrations.dataflows.article_body.enrich_ref_summary_from_url",
        lambda r: dict(r),
    )
    monkeypatch.setattr(
        "trade_integrations.dataflows.index_research.news_llm_story_pipeline.adjudication_summary_from_refs",
        lambda refs: {},
    )
    matched = {
        "canonical_story_id": "evt:existing",
        "title": "FII selling drags Nifty",
        "content_summary": "Foreign investors sold heavily.",
        "published_at": "2026-07-16",
        "tags": {"topics": ["fii"]},
        "structured_summary": {"facts": ["FII outflows"], "event_meta": {"references": []}},
    }
    monkeypatch.setattr(resolver, "_resolve_wiki_match", lambda *a, **k: (None, None))
    monkeypatch.setattr(resolver, "_list_match_candidates", lambda **k: [matched])
    monkeypatch.setattr(resolver, "find_matching_event", lambda ref, events, **k: matched)
    monkeypatch.setattr(resolver, "ref_adds_new_claims", lambda ref, event: False)
    monkeypatch.setattr(resolver, "get_event", lambda event_id: None)

    decision = resolver.resolve_staging_group(
        [
            {
                "ref_id": "ref:2",
                "title": "FII selling drags Nifty lower",
                "summary": "Foreign investors sold heavily in cash segment.",
                "url": "https://example.com/b",
                "published_at": "2026-07-16T10:00:00+00:00",
            }
        ],
        ticker="NIFTY",
    )
    assert decision.action == "discard"
    assert decision.reason == "syndicated_no_new_claims"
    assert decision.tier == "t3"


def test_gray_zone_agent_create_skips_rule_enrich(hub_tmp, monkeypatch):
    from trade_integrations.dataflows.index_research import news_resolver as resolver

    monkeypatch.setenv("MINIMAX_API_KEY", "test-key")
    monkeypatch.setattr(resolver, "filter_relevant_refs", lambda refs, **k: (refs, []))
    monkeypatch.setattr(
        "trade_integrations.dataflows.index_research.news_claim_extraction.enrich_ref_with_claims",
        lambda r: dict(r),
    )
    monkeypatch.setattr(
        "trade_integrations.dataflows.article_body.enrich_ref_summary_from_url",
        lambda r: dict(r),
    )
    monkeypatch.setattr(
        "trade_integrations.dataflows.index_research.news_llm_story_pipeline.adjudication_summary_from_refs",
        lambda refs: {},
    )
    candidate = {
        "canonical_story_id": "evt:gray",
        "title": "FII selling drags Nifty",
        "content_summary": "Foreign investors sold.",
        "published_at": "2026-07-16",
        "tags": {"topics": ["fii"]},
    }
    monkeypatch.setattr(resolver, "_resolve_wiki_match", lambda *a, **k: (None, None))
    monkeypatch.setattr(resolver, "_list_match_candidates", lambda **k: [candidate])
    monkeypatch.setattr(resolver, "find_matching_event", lambda ref, events, **k: None)
    monkeypatch.setattr(
        resolver,
        "_find_gray_zone_candidate",
        lambda ref, events, **k: (candidate, 0.65),
    )
    monkeypatch.setattr(
        "trade_integrations.dataflows.index_research.news_resolver_agent.adjudicate_gray_zone",
        lambda *a, **k: {"action": "create", "reason": "agent_distinct_story"},
    )
    monkeypatch.setattr(resolver, "ref_adds_new_claims", lambda ref, event: True)
    monkeypatch.setattr(resolver, "get_event", lambda event_id: None)

    decision = resolver.resolve_staging_group(
        [
            {
                "ref_id": "ref:agent-create",
                "title": "Distinct geopolitical shock",
                "summary": "Unrelated macro event.",
                "url": "https://example.com/distinct",
                "published_at": "2026-07-16T10:00:00+00:00",
            }
        ],
        ticker="NIFTY",
        t4_budget={"remaining": 1},
    )
    assert decision.action == "create"
    assert decision.reason == "no_match"
    assert decision.tier == "t3"


def test_gray_zone_rule_enrich_without_agent(hub_tmp, monkeypatch):
    from trade_integrations.dataflows.index_research import news_resolver as resolver

    monkeypatch.setenv("HUB_NEWS_RESOLVER_AGENT_ENABLED", "0")
    monkeypatch.setattr(resolver, "filter_relevant_refs", lambda refs, **k: (refs, []))
    monkeypatch.setattr(
        "trade_integrations.dataflows.index_research.news_claim_extraction.enrich_ref_with_claims",
        lambda r: dict(r),
    )
    monkeypatch.setattr(
        "trade_integrations.dataflows.article_body.enrich_ref_summary_from_url",
        lambda r: dict(r),
    )
    monkeypatch.setattr(
        "trade_integrations.dataflows.index_research.news_llm_story_pipeline.adjudication_summary_from_refs",
        lambda refs: {},
    )
    candidate = {
        "canonical_story_id": "evt:gray",
        "title": "FII selling drags Nifty",
        "content_summary": "Foreign investors sold.",
        "published_at": "2026-07-16",
        "tags": {"topics": ["fii"]},
        "structured_summary": {"facts": [], "event_meta": {"references": []}},
    }
    monkeypatch.setattr(resolver, "_resolve_wiki_match", lambda *a, **k: (None, None))
    monkeypatch.setattr(resolver, "_list_match_candidates", lambda **k: [candidate])
    monkeypatch.setattr(resolver, "find_matching_event", lambda ref, events, **k: None)
    monkeypatch.setattr(
        resolver,
        "_find_gray_zone_candidate",
        lambda ref, events, **k: (candidate, 0.65),
    )
    monkeypatch.setattr(resolver, "ref_adds_new_claims", lambda ref, event: True)
    monkeypatch.setattr(resolver, "get_event", lambda event_id: None)

    decision = resolver.resolve_staging_group(
        [
            {
                "ref_id": "ref:gray",
                "title": "FII selling drags Nifty lower",
                "summary": "New detail on outflows.",
                "url": "https://example.com/gray",
                "published_at": "2026-07-16T10:00:00+00:00",
            }
        ],
        ticker="NIFTY",
    )
    assert decision.action == "enrich"
    assert decision.reason == "gray_zone_rule_enrich"
    assert decision.tier == "t3"


def test_attach_refs_to_event(hub_tmp, monkeypatch):
    from trade_integrations.dataflows.index_research import news_resolver as resolver
    from trade_integrations.hub_storage import news_events_store as events_store
    from trade_integrations.hub_storage.news_event_models import DistilledNewsEvent

    events_store.upsert_event(
        DistilledNewsEvent(
            event_id="evt:attach",
            ticker="NIFTY",
            title="RBI holds rates",
            content="Policy unchanged.",
            publish_day="2026-07-16",
            structured_summary={"event_meta": {"references": [], "timeline": []}},
        )
    )
    result = resolver.attach_refs_to_event(
        refs=[
            {
                "ref_id": "ref:new",
                "title": "RBI unchanged",
                "summary": "Additional source confirms hold.",
                "url": "https://example.com/new",
                "source": "rss",
                "published_at": "2026-07-16T11:00:00+00:00",
            }
        ],
        event_id="evt:attach",
        ticker="NIFTY",
    )
    assert result.get("ok") is True
    assert result.get("attached") == 1
    stored = events_store.get_event("evt:attach")
    em = (stored.get("structured_summary") or {}).get("event_meta") or {}
    assert len(em.get("references") or []) == 1
