"""Tests for hub news pipeline step 01 — relevance gate."""

from __future__ import annotations

from trade_integrations.dataflows.index_research.hub_news_pipeline.pipeline_context import (
    RefPipelineContext,
)
from trade_integrations.dataflows.index_research.hub_news_pipeline.pipeline_runner import (
    run_ref_pipeline,
)
from trade_integrations.dataflows.index_research.hub_news_pipeline.step_01_relevance_gate import (
    run_step_01_relevance_gate,
)
from trade_integrations.dataflows.index_research.news_relevance import RelevanceVerdict


def _fake_assess(ref, *, ticker="NIFTY"):
    title = str(ref.get("title") or "").lower()
    if "cricket" in title:
        return RelevanceVerdict(
            relevant=False,
            confidence=0.92,
            reason="non-market",
            source="test",
        )
    if "nifty" in title:
        return RelevanceVerdict(
            relevant=True,
            confidence=0.88,
            reason="market",
            source="test",
        )
    return RelevanceVerdict(
        relevant=True,
        confidence=0.55,
        reason="ambiguous",
        source="test",
    )


def test_step_01_discards_irrelevant_high_confidence():
    ctx = RefPipelineContext(
        ref={"title": "IPL cricket final highlights", "summary": "Team wins"},
    )
    ctx, result = run_step_01_relevance_gate(ctx, assess_fn=_fake_assess, min_confidence=0.6)
    assert result.status == "discarded"
    assert ctx.should_continue is False
    assert ctx.discard_reason == "irrelevant_not_finance"


def test_step_01_passes_market_headline():
    ctx = RefPipelineContext(
        ref={"title": "NIFTY slips on FII selling", "summary": "Index down 0.5%"},
    )
    ctx, result = run_step_01_relevance_gate(ctx, assess_fn=_fake_assess, min_confidence=0.6)
    assert result.status == "ok"
    assert ctx.should_continue is True


def test_step_01_keeps_ambiguous_low_confidence_discard():
    ctx = RefPipelineContext(
        ref={"title": "Macro outlook mixed", "summary": "Global cues"},
    )
    ctx, result = run_step_01_relevance_gate(ctx, assess_fn=_fake_assess, min_confidence=0.6)
    assert result.status == "ok"
    assert ctx.should_continue is True


def test_step_01_skips_when_prefiltered():
    ctx = RefPipelineContext(
        ref={"title": "Cricket world cup", "summary": "Match today", "_relevance_prefiltered": True},
    )
    ctx, result = run_step_01_relevance_gate(ctx, assess_fn=_fake_assess, min_confidence=0.6)
    assert result.status == "ok"
    assert result.detail.get("reason") == "prefiltered_skip"
    assert ctx.should_continue is True


def test_pipeline_runner_stops_trace_on_discard():
    ref = {"title": "Cricket world cup", "summary": "Match today"}
    ctx = run_ref_pipeline(ref, through="step_01_relevance_gate")
    assert ctx.should_continue is False
    assert len(ctx.step_trace) == 1
    assert ctx.step_trace[0].step_id == "step_01_relevance_gate"
    assert ctx.step_trace[0].status == "discarded"


def test_pipeline_runner_marks_failed_step_as_stopped():
    ref = {
        "title": "NIFTY update",
        "summary": "short",
        "url": "https://example.com/a",
        "published_at": "2026-03-15",
    }

    def _llm(_prompt):
        raise RuntimeError("minimax down")

    ctx = run_ref_pipeline(
        ref,
        through="step_04_ref_enrich_llm",
        assess_fn=lambda r, ticker="NIFTY": RelevanceVerdict(relevant=True, confidence=0.9, reason="ok", source="test"),
        fetch_fn=lambda _u: "body " * 50,
        min_summary_len=400,
        llm_fn=_llm,
    )
    assert ctx.should_continue is False
    assert ctx.discard_reason.startswith("pipeline_step_failed:")
    assert ctx.step_trace[-1].status == "failed"
