"""Pipeline step 02b — Crawl4AI fetch when HTTP body is thin or failed."""

from __future__ import annotations

import time
from typing import Any

from trade_integrations.dataflows.index_research.hub_news_pipeline.pipeline_context import (
    RefPipelineContext,
    StepResult,
)
from trade_integrations.dataflows.index_research.hub_news_pipeline.step_02_fetch_http import (
    _MIN_BODY_LEN,
    _snippet_text,
)
from trade_integrations.dataflows.index_research.hub_news_pipeline.step_03_datetime_normalize import (
    extract_published_meta_from_html,
)

STEP_ID = "step_02b_fetch_crawl4ai"


def hub_news_crawl4ai_enabled() -> bool:
    import os

    return os.getenv("HUB_NEWS_CRAWL4AI_ENABLED", "0").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _default_crawl(urls: list[str]) -> list[Any]:
    from trade_integrations.dataflows.crawl4ai_client import crawl_urls_parallel_sync

    return crawl_urls_parallel_sync(urls)


def run_step_02b_fetch_crawl4ai(
    ctx: RefPipelineContext,
    *,
    crawl_fn: Any | None = None,
    min_body_len: int = _MIN_BODY_LEN,
    **_: Any,
) -> tuple[RefPipelineContext, StepResult]:
    """Upgrade to full body via Crawl4AI when HTTP fetch did not produce adequate text."""
    started = time.perf_counter()
    if not ctx.should_continue:
        result = StepResult(
            step_id=STEP_ID,
            status="skipped",
            detail={"reason": ctx.discard_reason or "pipeline_stopped"},
        )
        ctx.record_step(result)
        return ctx, result

    if not hub_news_crawl4ai_enabled():
        result = StepResult(
            step_id=STEP_ID,
            status="skipped",
            detail={"reason": "crawl4ai_disabled"},
        )
        ctx.record_step(result)
        return ctx, result

    if ctx.enrichment_mode == "full" and len(ctx.article_body or "") >= min_body_len:
        duration_ms = (time.perf_counter() - started) * 1000
        result = StepResult(
            step_id=STEP_ID,
            status="ok",
            duration_ms=duration_ms,
            detail={"reason": "http_body_sufficient", "skipped_crawl": True},
        )
        ctx.record_step(result)
        return ctx, result

    url = str(ctx.ref.get("url") or "").strip()
    if not url:
        result = StepResult(
            step_id=STEP_ID,
            status="skipped",
            detail={"reason": "missing_url"},
        )
        ctx.record_step(result)
        return ctx, result

    crawl = crawl_fn or _default_crawl
    duration_ms = (time.perf_counter() - started) * 1000
    try:
        results = crawl([url])
    except Exception as exc:
        duration_ms = (time.perf_counter() - started) * 1000
        result = StepResult(
            step_id=STEP_ID,
            status="ok",
            duration_ms=duration_ms,
            detail={
                "reason": "crawl_error",
                "enrichment_mode": ctx.enrichment_mode or "snippet_fallback",
                "error": str(exc)[:200],
            },
        )
        ctx.record_step(result)
        return ctx, result

    duration_ms = (time.perf_counter() - started) * 1000
    page = results[0] if results else None
    markdown = str(getattr(page, "markdown", "") or "").strip()
    meta = getattr(page, "metadata", None)
    html_hint = str((meta or {}).get("html") or "") if isinstance(meta, dict) else ""

    if page and getattr(page, "success", False) and len(markdown) >= min_body_len:
        ctx.article_body = markdown[:8000]
        ctx.enrichment_mode = "full"
        ctx.fetch_status = "ok"
        ctx.fetch_method = "crawl4ai"
        if html_hint:
            meta = extract_published_meta_from_html(html_hint)
            if meta:
                ctx.ref["_raw_html_meta_published"] = meta
        result = StepResult(
            step_id=STEP_ID,
            status="ok",
            duration_ms=duration_ms,
            detail={
                "enrichment_mode": "full",
                "body_len": len(ctx.article_body),
                "fetch_method": "crawl4ai",
            },
        )
        ctx.record_step(result)
        return ctx, result

    if not ctx.article_body:
        ctx.article_body = _snippet_text(ctx)
    if not ctx.enrichment_mode:
        ctx.enrichment_mode = "snippet_fallback"
    result = StepResult(
        step_id=STEP_ID,
        status="ok",
        duration_ms=duration_ms,
        detail={
            "reason": "crawl_failed_or_short",
            "enrichment_mode": ctx.enrichment_mode,
            "fetch_status": ctx.fetch_status or "failed",
        },
    )
    ctx.record_step(result)
    return ctx, result
