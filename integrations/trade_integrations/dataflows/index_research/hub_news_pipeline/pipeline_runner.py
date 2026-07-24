"""Orchestrate hub news pipeline steps with per-step tracing."""

from __future__ import annotations

import logging
from typing import Any, Callable

from trade_integrations.dataflows.index_research.hub_news_pipeline.pipeline_context import (
    RefPipelineContext,
    StepResult,
)
from trade_integrations.dataflows.index_research.hub_news_pipeline.step_01_relevance_gate import (
    run_step_01_relevance_gate,
)
from trade_integrations.dataflows.index_research.hub_news_pipeline.step_02_fetch_http import (
    run_step_02_fetch_http,
)
from trade_integrations.dataflows.index_research.hub_news_pipeline.step_02b_fetch_crawl4ai import (
    run_step_02b_fetch_crawl4ai,
)
from trade_integrations.dataflows.index_research.hub_news_pipeline.step_03_datetime_normalize import (
    run_step_03_datetime_normalize,
)
from trade_integrations.dataflows.index_research.hub_news_pipeline.step_04_ref_enrich_llm import (
    run_step_04_ref_enrich_llm,
)
from trade_integrations.dataflows.index_research.hub_news_pipeline.step_05_claims_bridge import (
    run_step_05_claims_bridge,
)
from trade_integrations.dataflows.index_research.hub_news_pipeline.step_06_adjudicate_bridge import (
    run_step_06_adjudicate_bridge,
)
from trade_integrations.dataflows.index_research.hub_news_pipeline.step_07_event_distill_bridge import (
    run_step_07_event_distill_bridge,
)

logger = logging.getLogger(__name__)

StepFn = Callable[..., tuple[RefPipelineContext, StepResult]]


def hub_news_pipeline_enabled() -> bool:
    import os

    return os.getenv("HUB_NEWS_PIPELINE_ENABLED", "1").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


STEP_REGISTRY: dict[str, StepFn] = {
    "step_01_relevance_gate": run_step_01_relevance_gate,
    "step_02_fetch_http": run_step_02_fetch_http,
    "step_02b_fetch_crawl4ai": run_step_02b_fetch_crawl4ai,
    "step_03_datetime_normalize": run_step_03_datetime_normalize,
    "step_04_ref_enrich_llm": run_step_04_ref_enrich_llm,
    "step_05_claims_bridge": run_step_05_claims_bridge,
    "step_06_adjudicate_bridge": run_step_06_adjudicate_bridge,
    "step_07_event_distill_bridge": run_step_07_event_distill_bridge,
}

DEFAULT_STEP_ORDER: list[str] = [
    "step_01_relevance_gate",
    "step_02_fetch_http",
    "step_02b_fetch_crawl4ai",
    "step_03_datetime_normalize",
    "step_04_ref_enrich_llm",
    "step_05_claims_bridge",
    "step_06_adjudicate_bridge",
    "step_07_event_distill_bridge",
]

RESOLVER_THROUGH = "step_07_event_distill_bridge"


def register_step(step_id: str, fn: StepFn) -> None:
    STEP_REGISTRY[step_id] = fn


def run_step(step_id: str, ctx: RefPipelineContext, **kwargs: Any) -> tuple[RefPipelineContext, StepResult]:
    fn = STEP_REGISTRY.get(step_id)
    if fn is None:
        result = StepResult(step_id=step_id, status="failed", error=f"unknown step: {step_id}")
        ctx.record_step(result)
        return ctx, result
    return fn(ctx, **kwargs)


def run_ref_pipeline(
    ref: dict[str, Any],
    *,
    ticker: str = "NIFTY",
    through: str | None = None,
    step_order: list[str] | None = None,
    **step_kwargs: Any,
) -> RefPipelineContext:
    """Run pipeline steps in order until `through` (inclusive) or all steps in order."""
    ctx = RefPipelineContext(ref=dict(ref), ticker=ticker)
    order = list(step_order or DEFAULT_STEP_ORDER)
    if through:
        if through not in order:
            order.append(through)
        idx = order.index(through)
        order = order[: idx + 1]

    for step_id in order:
        if not ctx.should_continue and step_id != "step_01_relevance_gate":
            skipped = StepResult(
                step_id=step_id,
                status="skipped",
                detail={"reason": ctx.discard_reason or "pipeline_stopped"},
            )
            ctx.record_step(skipped)
            continue
        try:
            ctx, result = run_step(step_id, ctx, **step_kwargs)
        except Exception as exc:
            logger.warning("hub news pipeline step %s failed: %s", step_id, exc)
            result = StepResult(step_id=step_id, status="failed", error=str(exc)[:200])
            ctx.record_step(result)
            ctx.should_continue = False
            ctx.discard_reason = f"pipeline_step_failed:{step_id}"
            break
        if result.status == "failed":
            ctx.should_continue = False
            ctx.discard_reason = ctx.discard_reason or f"pipeline_step_failed:{step_id}"
            break
        if result.status == "discarded":
            break
    return ctx
