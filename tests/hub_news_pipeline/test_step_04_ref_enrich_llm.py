"""Tests for hub news pipeline step 04 — ref enrich LLM."""

from __future__ import annotations

import json

from trade_integrations.dataflows.index_research.hub_news_pipeline.models import (
    normalize_article_enrichment,
    resolve_expected_date,
)
from trade_integrations.dataflows.index_research.hub_news_pipeline.pipeline_context import (
    RefPipelineContext,
)
from trade_integrations.dataflows.index_research.hub_news_pipeline.pipeline_runner import (
    run_ref_pipeline,
)
from trade_integrations.dataflows.index_research.hub_news_pipeline.step_04_ref_enrich_llm import (
    run_step_04_ref_enrich_llm,
)
from trade_integrations.dataflows.index_research.news_relevance import RelevanceVerdict


def test_resolve_expected_date_next_week():
    day, conf = resolve_expected_date(
        publish_day="2026-03-15",
        timeline_phrase="next week",
        expected_date="",
    )
    assert day == "2026-03-22"
    assert conf == "medium"


def test_normalize_moves_opinions_and_causes():
    raw = {
        "relevant": True,
        "cause_indicators": [
            {
                "factor": "fii_net_5d",
                "mechanism": "Foreign outflows pressure index",
                "direction_hint": "bearish",
                "confidence": 0.8,
            }
        ],
        "future_events": [
            {
                "event": "RBI MPC",
                "timeline_phrase": "next week",
                "index_impact_mechanism": "Rates affect banks",
            }
        ],
        "article_opinions": [{"kind": "price_prediction", "text": "NIFTY may hit 25000"}],
        "facts": [{"text": "FII sold 2400 cr"}],
        "distilled_summary": "FII selling ahead of RBI",
        "prediction_value_score": 0.75,
    }
    out = normalize_article_enrichment(
        raw,
        enrichment_mode="full",
        publish_day="2026-03-15",
        published_at="2026-03-15T09:15:00+05:30",
    )
    assert out["cause_indicators"][0]["factor"] == "fii_net_5d"
    assert out["future_events"][0]["expected_date"] == "2026-03-22"
    assert out["article_opinions"][0]["use_for_prediction"] is False


def test_normalize_empty_payload_not_relevant_by_default():
    out = normalize_article_enrichment(
        {},
        enrichment_mode="snippet_fallback",
        publish_day="2026-03-15",
        published_at="2026-03-15T09:15:00+05:30",
    )
    assert out["relevant"] is False


def test_step_04_idempotent_skip_discards_cached_irrelevant():
    ctx = RefPipelineContext(
        ref={
            "article_enrichment": {"relevant": False, "distilled_summary": ""},
        },
        enrichment_mode="snippet_fallback",
        publish_day="2026-03-15",
        published_at="2026-03-15T09:15:00+05:30",
    )
    ctx, result = run_step_04_ref_enrich_llm(ctx, llm_fn=lambda _p: "{}")
    assert result.status == "discarded"
    assert ctx.should_continue is False
    assert ctx.discard_reason == "irrelevant_after_enrichment"


def test_step_04_with_mock_llm():
    ctx = RefPipelineContext(
        ref={"title": "NIFTY falls on FII selling", "summary": "Index down"},
        enrichment_mode="snippet_fallback",
        publish_day="2026-03-15",
        published_at="2026-03-15T09:15:00+05:30",
        article_body="NIFTY falls on FII selling\n\nIndex down",
    )

    payload = {
        "relevant": True,
        "cause_indicators": [
            {
                "factor": "fii_net_5d",
                "mechanism": "FII selling",
                "direction_hint": "bearish",
                "confidence": 0.7,
            }
        ],
        "future_events": [],
        "article_opinions": [],
        "facts": [],
        "distilled_summary": "FII driven selloff",
        "prediction_value_score": 0.6,
    }

    def _llm(_prompt):
        return json.dumps(payload)

    ctx, result = run_step_04_ref_enrich_llm(ctx, llm_fn=_llm)
    assert result.status == "ok"
    assert ctx.article_enrichment["cause_indicators"][0]["factor"] == "fii_net_5d"
    assert ctx.ref["summary"] == "FII driven selloff"


def test_step_04_discards_when_llm_marks_irrelevant():
    ctx = RefPipelineContext(
        ref={"title": "Cricket", "summary": "Match"},
        enrichment_mode="snippet_fallback",
        publish_day="2026-03-15",
        published_at="2026-03-15T09:15:00+05:30",
        article_body="Cricket match",
    )

    def _llm(_prompt):
        return json.dumps({"relevant": False, "distilled_summary": ""})

    ctx, result = run_step_04_ref_enrich_llm(ctx, llm_fn=_llm)
    assert result.status == "discarded"
    assert ctx.should_continue is False


def _fake_assess(ref, *, ticker="NIFTY"):
    return RelevanceVerdict(relevant=True, confidence=0.9, reason="ok", source="test")


def test_pipeline_through_step_04():
    ref = {
        "title": "NIFTY weak on FII outflows",
        "summary": "short",
        "url": "https://example.com/a",
        "published_at": "2026-03-15",
    }

    def _fetch(_url):
        return "FII sold heavily. RBI meets next week."

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

    ctx = run_ref_pipeline(
        ref,
        through="step_04_ref_enrich_llm",
        assess_fn=_fake_assess,
        fetch_fn=_fetch,
        min_summary_len=400,
        llm_fn=_llm,
    )
    assert len(ctx.step_trace) == 5
    assert ctx.step_trace[-1].step_id == "step_04_ref_enrich_llm"
    assert ctx.article_enrichment["future_events"][0]["expected_date"] == "2026-03-22"
