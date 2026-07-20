"""Tests for fact-first LLM story adjudication + dedup pipeline."""

from __future__ import annotations

import pytest


@pytest.fixture
def hub_tmp(tmp_path, monkeypatch):
    from trade_integrations.context import hub as hub_mod

    hub = tmp_path / "hub"
    hub.mkdir()
    monkeypatch.setattr(hub_mod, "get_hub_dir", lambda: hub)
    return hub


def test_parse_adjudication_json_with_fences():
    from trade_integrations.dataflows.index_research.news_llm_story_pipeline import (
        _parse_adjudication_response,
    )

    raw = """```json
[
  {
    "ref_id": "ref:a",
    "claims": [{"type": "oil_price", "value": "Brent > 90", "quote": "oil surged"}],
    "tape_alignment": "supported",
    "credibility": "valid",
    "discard": false,
    "story_fingerprint": "2026-07-20|oil_risk"
  }
]
```"""
    verdicts = _parse_adjudication_response(raw, {"ref:a"})
    assert len(verdicts) == 1
    assert verdicts[0].ref_id == "ref:a"
    assert verdicts[0].credibility == "valid"
    assert verdicts[0].story_fingerprint == "2026-07-20|oil_risk"


def test_parse_story_groups_assigns_missing_singletons():
    from trade_integrations.dataflows.index_research.news_llm_story_pipeline import (
        _parse_story_groups,
    )

    raw = '[{"group_id":"g1","ref_ids":["ref:a"],"story_fingerprint":"fp1","shared_facts":["x"],"headline_hint":"h","why_grouped":"w"}]'
    groups = _parse_story_groups(raw, {"ref:a", "ref:b"})
    assert len(groups) == 2
    ids = {rid for g in groups for rid in g["ref_ids"]}
    assert ids == {"ref:a", "ref:b"}


def test_rule_prefilter_empty_discards():
    from trade_integrations.dataflows.index_research.news_llm_story_pipeline import (
        rule_prefilter_adjudication,
    )

    verdict = rule_prefilter_adjudication({"ref_id": "ref:x", "title": "", "summary": ""})
    assert verdict is not None
    assert verdict.discard is True
    assert verdict.credibility == "irrelevant"


def test_apply_adjudication_discards(hub_tmp, monkeypatch):
    from trade_integrations.dataflows.index_research.news_llm_story_pipeline import (
        AdjudicationVerdict,
        apply_adjudication_discards,
    )
    from trade_integrations.hub_storage import news_staging_store as staging

    monkeypatch.setattr(staging, "get_hub_dir", lambda: hub_tmp)
    ref_id, _ = staging.enqueue_raw_ref(
        {"title": "Hoax headline", "summary": "Fake", "url": "https://example.com/hoax"},
        ticker="NIFTY",
    )
    ref = staging.list_pending_refs(ticker="NIFTY")[0]
    verdicts = [
        AdjudicationVerdict(
            ref_id=ref_id,
            credibility="likely_hoax",
            tape_alignment="contradicted",
            discard=True,
            discard_reason="test hoax",
        )
    ]
    kept, discarded = apply_adjudication_discards([ref], verdicts)
    assert discarded == 1
    assert kept == []
    assert staging.list_pending_refs(ticker="NIFTY") == []
    rows = staging.list_discarded_refs(ticker="NIFTY")
    assert rows and rows[0].get("source_kind") == "adjudication"


def test_mechanical_story_groups_by_fingerprint():
    from trade_integrations.dataflows.index_research.news_llm_story_pipeline import (
        AdjudicationVerdict,
        mechanical_story_groups,
    )

    refs = [
        {"ref_id": "ref:a", "title": "Oil spike hits markets", "published_at": "2026-07-20"},
        {"ref_id": "ref:b", "title": "Crude rally weighs on Nifty", "published_at": "2026-07-20"},
        {"ref_id": "ref:c", "title": "RBI holds repo", "published_at": "2026-07-20"},
    ]
    adjudications = [
        AdjudicationVerdict(ref_id="ref:a", story_fingerprint="2026-07-20|oil"),
        AdjudicationVerdict(ref_id="ref:b", story_fingerprint="2026-07-20|oil"),
        AdjudicationVerdict(ref_id="ref:c", story_fingerprint="2026-07-20|rbi"),
    ]
    groups = mechanical_story_groups(refs, adjudications)
    assert len(groups) == 2
    by_size = sorted(len(g["ref_ids"]) for g in groups)
    assert by_size == [1, 2]


def test_run_story_pipeline_disabled_uses_singletons(monkeypatch):
    from trade_integrations.dataflows.index_research import news_llm_story_pipeline as pipe

    monkeypatch.setattr(pipe, "llm_adjudication_enabled", lambda: False)
    monkeypatch.setattr(pipe, "pre_enrich_refs_for_adjudication", lambda refs: refs)

    refs = [
        {"ref_id": "ref:a", "title": "One"},
        {"ref_id": "ref:b", "title": "Two"},
    ]
    groups, stats = pipe.run_story_pipeline_batch(refs, market_context={})
    assert len(groups) == 2
    assert stats["story_groups_fallback"] is True


def test_parse_adjudication_maps_placeholder_ref_ids():
    from trade_integrations.dataflows.index_research.news_llm_story_pipeline import (
        _parse_adjudication_response,
    )

    raw = (
        '[{"ref_id":"id1","claims":[],"tape_alignment":"supported","credibility":"valid",'
        '"discard":false,"story_fingerprint":"fp1"}]'
    )
    verdicts = _parse_adjudication_response(raw, {"ref:real"})
    assert len(verdicts) == 1
    assert verdicts[0].ref_id == "ref:real"


def test_run_story_pipeline_mock_llm(monkeypatch):
    from trade_integrations.dataflows.index_research import news_llm_story_pipeline as pipe

    monkeypatch.setattr(pipe, "llm_adjudication_enabled", lambda: True)
    monkeypatch.setattr(pipe, "pre_enrich_refs_for_adjudication", lambda refs: refs)
    monkeypatch.setattr(pipe, "adjudication_batch_size", lambda: 8)
    monkeypatch.setattr(
        "trade_integrations.hub_storage.news_staging_store.require_minimax_for_distillation",
        lambda: None,
    )

    def fake_call(prompt, max_tokens=None):
        if "adjudicate" in prompt.lower():
            return (
                '[{"ref_id":"ref:a","claims":[],"tape_alignment":"supported",'
                '"credibility":"valid","discard":false,"story_fingerprint":"fp|a"},'
                '{"ref_id":"ref:b","claims":[],"tape_alignment":"supported",'
                '"credibility":"valid","discard":false,"story_fingerprint":"fp|a"}]'
            )
        return (
            '[{"group_id":"g1","ref_ids":["ref:a","ref:b"],"story_fingerprint":"fp|a",'
            '"shared_facts":["same story"],"headline_hint":"h","why_grouped":"same"}]'
        )

    monkeypatch.setattr(
        "trade_integrations.dataflows.index_research.news_llm_story_pipeline.call_minimax_json_text",
        fake_call,
    )

    refs = [
        {"ref_id": "ref:a", "title": "Story A", "summary": "s", "published_at": "2026-07-20"},
        {"ref_id": "ref:b", "title": "Story B", "summary": "s", "published_at": "2026-07-20"},
    ]
    groups, stats = pipe.run_story_pipeline_batch(refs, market_context={"factors": {}})
    assert stats["llm_dedup_groups"] == 1
    assert len(groups[0]["ref_ids"]) == 2


def test_distill_event_accepts_adjudication_summary(monkeypatch):
    from trade_integrations.dataflows.index_research.news_distillation import distill_event

    captured = {}

    def fake_llm(**kwargs):
        captured.update(kwargs)
        return {"title": "T", "content": "Body"}

    monkeypatch.setattr(
        "trade_integrations.hub_storage.news_staging_store.minimax_configured",
        lambda: True,
    )
    monkeypatch.setattr(
        "trade_integrations.dataflows.index_research.news_distillation._llm_distill",
        fake_llm,
    )

    summary = {"credibility": "exaggeration", "story_fingerprint": "fp1", "shared_facts": ["oil up"]}
    out = distill_event(
        refs=[{"title": "Headline", "summary": "text", "adjudication": {"ref_id": "r1"}}],
        adjudication_summary=summary,
    )
    assert captured.get("adjudication_summary") == summary
    em = (out.get("structured_summary") or {}).get("event_meta") or {}
    assert em.get("adjudication_summary") == summary
