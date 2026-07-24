"""Pipeline step 04 — LLM ref enrichment (causes + future timeline)."""

from __future__ import annotations

import logging
import os
import time
from typing import Any

from trade_integrations.dataflows.index_research.hub_news_pipeline.models import (
    parse_enrichment_response,
)
from trade_integrations.dataflows.index_research.hub_news_pipeline.pipeline_context import (
    RefPipelineContext,
    StepResult,
)

logger = logging.getLogger(__name__)

STEP_ID = "step_04_ref_enrich_llm"


def article_distill_enabled() -> bool:
    raw = os.getenv("HUB_NEWS_ARTICLE_DISTILL_ENABLED", "1").strip().lower()
    return raw in {"1", "true", "yes", "on"}


def _input_text(ctx: RefPipelineContext) -> str:
    title = str(ctx.ref.get("title") or "").strip()
    if ctx.enrichment_mode == "full" and ctx.article_body:
        body = ctx.article_body[:8000]
        return f"Title: {title}\n\nArticle body:\n{body}"
    summary = str(ctx.ref.get("summary") or "").strip()
    return f"Title: {title}\n\nRSS snippet:\n{summary}"


def build_enrichment_prompt(ctx: RefPipelineContext) -> str:
    mode = ctx.enrichment_mode or "snippet_fallback"
    conservative = (
        "You only have title + RSS snippet. Be conservative: lower confidence, "
        "do not invent numbers not in the snippet."
        if mode == "snippet_fallback"
        else
        "You have the article body. Extract causal mechanisms and dated future events precisely."
    )
    return (
        "You enrich Indian market news for NIFTY index prediction research.\n"
        "Extract CAUSES (factor mechanisms that could move the index) and FUTURE EVENTS (dated/upcoming).\n"
        "Move explicit price targets / NIFTY level predictions to article_opinions with use_for_prediction=false.\n"
        "Do NOT treat article price predictions as hub signals.\n"
        f"{conservative}\n"
        f"Article publish_day (IST): {ctx.publish_day}\n"
        f"Article published_at: {ctx.published_at}\n\n"
        "Output JSON only with keys:\n"
        "relevant (bool), cause_indicators [{factor, mechanism, direction_hint, confidence, evidence_quote}],\n"
        "future_events [{event, timeline_phrase, expected_date, date_confidence, index_impact_mechanism}],\n"
        "article_opinions [{kind, text, reason_discarded}],\n"
        "facts [{text, as_of}], distilled_summary (string), prediction_value_score (0-1).\n\n"
        f"{_input_text(ctx)}"
    )


def run_step_04_ref_enrich_llm(
    ctx: RefPipelineContext,
    *,
    llm_fn: Any | None = None,
    enabled: bool | None = None,
    **_: Any,
) -> tuple[RefPipelineContext, StepResult]:
    started = time.perf_counter()
    if not ctx.should_continue:
        result = StepResult(
            step_id=STEP_ID,
            status="skipped",
            detail={"reason": ctx.discard_reason or "pipeline_stopped"},
        )
        ctx.record_step(result)
        return ctx, result

    if enabled is None:
        enabled = article_distill_enabled()
    if not enabled:
        ctx.enrichment_mode = ctx.enrichment_mode or "snippet_fallback"
        ctx.article_enrichment = {
            "relevant": True,
            "enrichment_mode": ctx.enrichment_mode,
            "distilled_summary": str(ctx.ref.get("summary") or ctx.ref.get("title") or "")[:600],
            "cause_indicators": [],
            "future_events": [],
            "article_opinions": [],
            "facts": [],
            "prediction_value_score": 0.0,
        }
        result = StepResult(
            step_id=STEP_ID,
            status="skipped",
            detail={"reason": "HUB_NEWS_ARTICLE_DISTILL_ENABLED=0"},
        )
        ctx.record_step(result)
        return ctx, result

    if ctx.ref.get("article_enrichment") and not ctx.ref.get("_force_re_enrich"):
        existing = dict(ctx.ref.get("article_enrichment") or {})
        if existing.get("relevant") is False:
            ctx.should_continue = False
            ctx.discard_reason = "irrelevant_after_enrichment"
            ctx.article_enrichment = existing
            ctx.ref["article_enrichment"] = existing
            duration_ms = (time.perf_counter() - started) * 1000
            result = StepResult(
                step_id=STEP_ID,
                status="discarded",
                duration_ms=duration_ms,
                detail={"reason": "cached_irrelevant", "cached": True},
            )
            ctx.record_step(result)
            return ctx, result
        ctx.article_enrichment = existing
        result = StepResult(
            step_id=STEP_ID,
            status="ok",
            detail={"reason": "idempotent_skip", "cached": True},
        )
        ctx.record_step(result)
        return ctx, result

    mode = ctx.enrichment_mode or "snippet_fallback"
    prompt = build_enrichment_prompt(ctx)

    try:
        if llm_fn is not None:
            raw_text = llm_fn(prompt)
        else:
            from trade_integrations.dataflows.index_research.news_distillation import (
                call_minimax_json_text,
            )

            raw_text = call_minimax_json_text(prompt, max_tokens=2048)
        enrichment = parse_enrichment_response(
            raw_text,
            enrichment_mode=mode,
            publish_day=ctx.publish_day,
            published_at=ctx.published_at,
        )
    except Exception as exc:
        logger.warning("step_04_ref_enrich_llm failed: %s", exc)
        duration_ms = (time.perf_counter() - started) * 1000
        result = StepResult(step_id=STEP_ID, status="failed", duration_ms=duration_ms, error=str(exc)[:200])
        ctx.record_step(result)
        return ctx, result

    if not enrichment.get("relevant"):
        ctx.should_continue = False
        ctx.discard_reason = "irrelevant_after_enrichment"
        ctx.article_enrichment = enrichment
        ctx.ref["article_enrichment"] = enrichment
        duration_ms = (time.perf_counter() - started) * 1000
        result = StepResult(
            step_id=STEP_ID,
            status="discarded",
            duration_ms=duration_ms,
            detail={"relevant": False},
        )
        ctx.record_step(result)
        return ctx, result

    ctx.article_enrichment = enrichment
    ctx.ref["article_enrichment"] = enrichment
    if enrichment.get("distilled_summary"):
        ctx.ref["summary"] = str(enrichment["distilled_summary"])[:2000]

    duration_ms = (time.perf_counter() - started) * 1000
    result = StepResult(
        step_id=STEP_ID,
        status="ok",
        duration_ms=duration_ms,
        detail={
            "cause_count": len(enrichment.get("cause_indicators") or []),
            "future_event_count": len(enrichment.get("future_events") or []),
            "opinion_count": len(enrichment.get("article_opinions") or []),
            "prediction_value_score": enrichment.get("prediction_value_score"),
        },
    )
    ctx.record_step(result)
    return ctx, result
