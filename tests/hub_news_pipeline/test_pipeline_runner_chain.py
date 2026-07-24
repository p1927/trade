"""Integration tests for hub news pipeline runner chain."""

from __future__ import annotations

import json

from trade_integrations.dataflows.index_research.hub_news_pipeline.pipeline_runner import (
    DEFAULT_STEP_ORDER,
    RESOLVER_THROUGH,
    run_ref_pipeline,
)
from trade_integrations.dataflows.index_research.news_llm_story_pipeline import AdjudicationVerdict
from trade_integrations.dataflows.index_research.news_relevance import RelevanceVerdict


def _fake_assess(ref, *, ticker="NIFTY"):
    return RelevanceVerdict(relevant=True, confidence=0.9, reason="ok", source="test")


def test_default_step_order_includes_02b_through_07():
    assert "step_02b_fetch_crawl4ai" in DEFAULT_STEP_ORDER
    assert DEFAULT_STEP_ORDER.index("step_02b_fetch_crawl4ai") == 2
    assert DEFAULT_STEP_ORDER[-1] == "step_07_event_distill_bridge"
    assert RESOLVER_THROUGH == "step_07_event_distill_bridge"


def test_pipeline_chain_through_resolver(monkeypatch):
    monkeypatch.setenv("HUB_NEWS_CRAWL4AI_ENABLED", "0")
    monkeypatch.setenv("HUB_NEWS_LLM_ADJUDICATION_ENABLED", "1")
    monkeypatch.setenv("HUB_NEWS_ARTICLE_DISTILL_ENABLED", "1")

    ref = {
        "ref_id": "ref:chain",
        "title": "NIFTY weak on FII outflows",
        "summary": "short",
        "url": "https://example.com/a",
        "published_at": "2026-03-15",
    }

    html = (
        '<html><meta property="article:published_time" content="2026-03-15T10:00:00+05:30"/>'
        "<body>FII sold heavily. RBI meets next week.</body></html>"
    )

    def _fetch(_url):
        return ("FII sold heavily. RBI meets next week. " * 8, html)

    def _llm(_prompt):
        return json.dumps(
            {
                "relevant": True,
                "cause_indicators": [{"factor": "fii_net_5d", "mechanism": "outflows"}],
                "future_events": [{"event": "RBI MPC", "timeline_phrase": "next week"}],
                "article_opinions": [],
                "facts": [],
                "distilled_summary": "FII + RBI week ahead",
                "prediction_value_score": 0.8,
            }
        )

    def _adjudicate(refs, **kwargs):
        rid = str(refs[0].get("ref_id") or "ref:chain")
        return (
            [AdjudicationVerdict(ref_id=rid, credibility="valid", discard=False)],
            {"llm_ok": 1},
        )

    ctx = run_ref_pipeline(
        ref,
        through=RESOLVER_THROUGH,
        skip_if_prefiltered=True,
        assess_fn=_fake_assess,
        fetch_fn=_fetch,
        min_summary_len=400,
        llm_fn=_llm,
        adjudicate_fn=_adjudicate,
    )

    assert ctx.should_continue is True
    assert len(ctx.step_trace) == len(DEFAULT_STEP_ORDER)
    assert ctx.step_trace[2].step_id == "step_02b_fetch_crawl4ai"
    assert ctx.step_trace[2].status == "skipped"
    assert ctx.ref.get("article_enrichment")
    assert ctx.ref.get("pipeline_distill_hints")
    assert ctx.ref.get("structured_enrichment")
    assert ctx.ref.get("adjudication")
    assert ctx.ref.get("_raw_html_meta_published")
