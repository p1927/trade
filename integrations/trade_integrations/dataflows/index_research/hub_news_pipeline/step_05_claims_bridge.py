"""Pipeline step 05 — bridge article_enrichment onto ref + claim extraction."""

from __future__ import annotations

import time
from typing import Any

from trade_integrations.dataflows.index_research.hub_news_pipeline.pipeline_context import (
    RefPipelineContext,
    StepResult,
)

STEP_ID = "step_05_claims_bridge"


def run_step_05_claims_bridge(
    ctx: RefPipelineContext,
    *,
    enrich_claims_fn: Any | None = None,
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

    enrichment = dict(ctx.article_enrichment or ctx.ref.get("article_enrichment") or {})
    if enrichment:
        ctx.ref["article_enrichment"] = enrichment
        ctx.ref["pipeline_enrichment_mode"] = enrichment.get("enrichment_mode") or ctx.enrichment_mode
        if enrichment.get("distilled_summary"):
            ctx.ref["summary"] = str(enrichment["distilled_summary"])[:2000]

    from trade_integrations.dataflows.index_research.news_claim_extraction import (
        enrich_ref_with_claims,
    )

    enrich = enrich_claims_fn or enrich_ref_with_claims
    ctx.ref = enrich(dict(ctx.ref))

    duration_ms = (time.perf_counter() - started) * 1000
    claims = ctx.ref.get("extracted_claims") or []
    result = StepResult(
        step_id=STEP_ID,
        status="ok",
        duration_ms=duration_ms,
        detail={
            "claim_count": len(claims) if isinstance(claims, list) else 0,
            "cause_count": len(enrichment.get("cause_indicators") or []),
            "future_event_count": len(enrichment.get("future_events") or []),
        },
    )
    ctx.record_step(result)
    return ctx, result
