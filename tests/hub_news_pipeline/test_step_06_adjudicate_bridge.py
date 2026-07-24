"""Tests for hub news pipeline step 06 — adjudicate bridge."""

from __future__ import annotations

from trade_integrations.dataflows.index_research.hub_news_pipeline.pipeline_context import (
    RefPipelineContext,
)
from trade_integrations.dataflows.index_research.hub_news_pipeline.step_06_adjudicate_bridge import (
    run_step_06_adjudicate_bridge,
)
from trade_integrations.dataflows.index_research.news_llm_story_pipeline import AdjudicationVerdict


def test_step_06_skipped_when_adjudication_disabled(monkeypatch):
    monkeypatch.setenv("HUB_NEWS_LLM_ADJUDICATION_ENABLED", "0")
    ctx = RefPipelineContext(ref={"title": "NIFTY update", "ref_id": "ref:1"})
    ctx, result = run_step_06_adjudicate_bridge(ctx)
    assert result.status == "skipped"
    assert result.detail.get("reason") == "llm_adjudication_disabled"


def test_step_06_attaches_verdict(monkeypatch):
    monkeypatch.setenv("HUB_NEWS_LLM_ADJUDICATION_ENABLED", "1")
    ctx = RefPipelineContext(
        ref={
            "ref_id": "ref:1",
            "title": "NIFTY falls",
            "summary": "FII selling",
            "article_enrichment": {
                "cause_indicators": [{"factor": "fii_net_5d", "mechanism": "outflows"}],
            },
        },
    )

    def _adjudicate(refs, **kwargs):
        return (
            [
                AdjudicationVerdict(
                    ref_id="ref:1",
                    credibility="valid",
                    tape_alignment="aligned",
                    discard=False,
                )
            ],
            {"llm_ok": 1},
        )

    ctx, result = run_step_06_adjudicate_bridge(ctx, adjudicate_fn=_adjudicate)
    assert result.status == "ok"
    assert ctx.ref["adjudication"]["credibility"] == "valid"
    assert ctx.ref["adjudication"]["cause_aware"] is True
    assert "[cause_indicators:" not in ctx.ref["summary"]


def test_step_06_discards_on_verdict(monkeypatch):
    monkeypatch.setenv("HUB_NEWS_LLM_ADJUDICATION_ENABLED", "1")
    ctx = RefPipelineContext(ref={"ref_id": "ref:2", "title": "Fake news"})

    def _adjudicate(refs, **kwargs):
        return (
            [
                AdjudicationVerdict(
                    ref_id="ref:2",
                    credibility="likely_hoax",
                    discard=True,
                    discard_reason="hoax",
                )
            ],
            {},
        )

    ctx, result = run_step_06_adjudicate_bridge(ctx, adjudicate_fn=_adjudicate)
    assert result.status == "discarded"
    assert ctx.should_continue is False
