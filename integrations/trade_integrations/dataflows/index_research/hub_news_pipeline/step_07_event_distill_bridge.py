"""Pipeline step 07 — bridge enrichment into event distillation."""

from __future__ import annotations

import time
from typing import Any

from trade_integrations.dataflows.index_research.hub_news_pipeline.pipeline_context import (
    RefPipelineContext,
    StepResult,
)

STEP_ID = "step_07_event_distill_bridge"


def format_enrichment_distill_block(enrichment: dict[str, Any]) -> str:
    lines: list[str] = []
    for row in enrichment.get("cause_indicators") or []:
        if not isinstance(row, dict):
            continue
        factor = str(row.get("factor") or "").strip()
        mechanism = str(row.get("mechanism") or "").strip()
        direction = str(row.get("direction_hint") or "").strip()
        if factor or mechanism:
            lines.append(f"cause ({factor}): {mechanism} [{direction}]".strip())
    for row in enrichment.get("future_events") or []:
        if not isinstance(row, dict):
            continue
        event = str(row.get("event") or "").strip()
        when = str(row.get("expected_date") or row.get("timeline_phrase") or "").strip()
        mechanism = str(row.get("index_impact_mechanism") or "").strip()
        if event:
            lines.append(f"upcoming ({when}): {event} — {mechanism}".strip(" —"))
    return "\n".join(lines[:16])


def run_step_07_event_distill_bridge(
    ctx: RefPipelineContext,
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
    block = format_enrichment_distill_block(enrichment)
    if block:
        ctx.ref["pipeline_distill_hints"] = block

    ctx.ref["structured_enrichment"] = {
        "cause_indicators": list(enrichment.get("cause_indicators") or []),
        "future_events": list(enrichment.get("future_events") or []),
        "article_opinions": list(enrichment.get("article_opinions") or []),
    }

    duration_ms = (time.perf_counter() - started) * 1000
    result = StepResult(
        step_id=STEP_ID,
        status="ok",
        duration_ms=duration_ms,
        detail={
            "hint_lines": len(block.splitlines()) if block else 0,
            "cause_count": len(ctx.ref["structured_enrichment"]["cause_indicators"]),
            "future_event_count": len(ctx.ref["structured_enrichment"]["future_events"]),
        },
    )
    ctx.record_step(result)
    return ctx, result
