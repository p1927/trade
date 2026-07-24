"""Pipeline step 06 — bridge per-ref LLM adjudication."""

from __future__ import annotations

import time
from typing import Any

from trade_integrations.dataflows.index_research.hub_news_pipeline.pipeline_context import (
    RefPipelineContext,
    StepResult,
)

STEP_ID = "step_06_adjudicate_bridge"


def _cause_hint(enrichment: dict[str, Any]) -> str:
    causes = enrichment.get("cause_indicators") or []
    if not isinstance(causes, list) or not causes:
        return ""
    bits = []
    for row in causes[:5]:
        if not isinstance(row, dict):
            continue
        factor = str(row.get("factor") or "").strip()
        mechanism = str(row.get("mechanism") or "").strip()
        if factor or mechanism:
            bits.append(f"{factor}: {mechanism}".strip(": "))
    return "; ".join(bits)


def run_step_06_adjudicate_bridge(
    ctx: RefPipelineContext,
    *,
    adjudicate_fn: Any | None = None,
    market_context: dict[str, Any] | None = None,
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

    existing = ctx.ref.get("adjudication")
    if isinstance(existing, dict) and existing.get("ref_id"):
        has_enrichment = bool(ctx.article_enrichment or ctx.ref.get("article_enrichment"))
        if existing.get("cause_aware") or not has_enrichment:
            duration_ms = (time.perf_counter() - started) * 1000
            result = StepResult(
                step_id=STEP_ID,
                status="ok",
                duration_ms=duration_ms,
                detail={"reason": "idempotent_skip", "cached": True},
            )
            ctx.record_step(result)
            return ctx, result

    from trade_integrations.dataflows.index_research.news_llm_story_pipeline import (
        llm_adjudicate_refs,
        llm_adjudication_enabled,
    )

    if not llm_adjudication_enabled():
        result = StepResult(
            step_id=STEP_ID,
            status="skipped",
            detail={"reason": "llm_adjudication_disabled"},
        )
        ctx.record_step(result)
        return ctx, result

    ref = dict(ctx.ref)
    if not str(ref.get("ref_id") or "").strip():
        ref["ref_id"] = f"ref:pipeline:{hash(ref.get('url') or ref.get('title') or 'x') & 0xFFFF:x}"

    enrichment = ref.get("article_enrichment") if isinstance(ref.get("article_enrichment"), dict) else {}
    hint = _cause_hint(enrichment)
    if hint:
        ref["summary"] = f"{ref.get('summary') or ''}\n[cause_indicators: {hint}]".strip()[:2400]

    adjudicate = adjudicate_fn or llm_adjudicate_refs
    verdicts, stats = adjudicate([ref], market_context=market_context)
    duration_ms = (time.perf_counter() - started) * 1000

    if not verdicts:
        result = StepResult(
            step_id=STEP_ID,
            status="ok",
            duration_ms=duration_ms,
            detail={"reason": "no_verdict", "stats": stats},
        )
        ctx.record_step(result)
        return ctx, result

    verdict = verdicts[0]
    adj_dict = verdict.to_dict()
    adj_dict["cause_aware"] = bool(hint or enrichment.get("cause_indicators"))
    ctx.ref["adjudication"] = adj_dict
    if verdict.discard:
        ctx.should_continue = False
        ctx.discard_reason = verdict.discard_reason or "adjudication_discard"
        result = StepResult(
            step_id=STEP_ID,
            status="discarded",
            duration_ms=duration_ms,
            detail={"credibility": verdict.credibility, "tape_alignment": verdict.tape_alignment},
        )
        ctx.record_step(result)
        return ctx, result

    result = StepResult(
        step_id=STEP_ID,
        status="ok",
        duration_ms=duration_ms,
        detail={
            "credibility": verdict.credibility,
            "tape_alignment": verdict.tape_alignment,
            "stats": stats,
        },
    )
    ctx.record_step(result)
    return ctx, result
