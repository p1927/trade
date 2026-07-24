"""Pipeline step 01 — finance/markets relevance gate."""

from __future__ import annotations

import time
from typing import Any

from trade_integrations.dataflows.index_research.hub_news_pipeline.pipeline_context import (
    RefPipelineContext,
    StepResult,
)

STEP_ID = "step_01_relevance_gate"


def run_step_01_relevance_gate(
    ctx: RefPipelineContext,
    *,
    assess_fn: Any | None = None,
    min_confidence: float | None = None,
    skip_if_prefiltered: bool = False,
    **_: Any,
) -> tuple[RefPipelineContext, StepResult]:
    """Gate non-finance refs before fetch/LLM. LLM used for ambiguous refs via assess_ref_relevance."""
    started = time.perf_counter()
    if not ctx.should_continue:
        result = StepResult(
            step_id=STEP_ID,
            status="skipped",
            duration_ms=0.0,
            detail={"reason": "pipeline_already_stopped"},
        )
        ctx.record_step(result)
        return ctx, result

    if skip_if_prefiltered or ctx.ref.get("_relevance_prefiltered"):
        duration_ms = (time.perf_counter() - started) * 1000
        result = StepResult(
            step_id=STEP_ID,
            status="ok",
            duration_ms=duration_ms,
            detail={"reason": "prefiltered_skip"},
        )
        ctx.record_step(result)
        return ctx, result

    from trade_integrations.dataflows.index_research.news_relevance import (
        assess_ref_relevance,
        relevance_min_confidence,
    )

    assess = assess_fn or assess_ref_relevance
    threshold = relevance_min_confidence() if min_confidence is None else min_confidence
    verdict = assess(ctx.ref, ticker=ctx.ticker)
    ctx.relevance_verdict = verdict.to_dict()

    if not verdict.relevant and verdict.confidence >= threshold:
        ctx.should_continue = False
        ctx.discard_reason = "irrelevant_not_finance"
        duration_ms = (time.perf_counter() - started) * 1000
        result = StepResult(
            step_id=STEP_ID,
            status="discarded",
            duration_ms=duration_ms,
            detail={
                "relevant": False,
                "confidence": verdict.confidence,
                "reason": verdict.reason,
                "source": verdict.source,
            },
        )
        ctx.record_step(result)
        return ctx, result

    duration_ms = (time.perf_counter() - started) * 1000
    result = StepResult(
        step_id=STEP_ID,
        status="ok",
        duration_ms=duration_ms,
        detail={
            "relevant": verdict.relevant,
            "confidence": verdict.confidence,
            "reason": verdict.reason,
            "source": verdict.source,
        },
    )
    ctx.record_step(result)
    return ctx, result
