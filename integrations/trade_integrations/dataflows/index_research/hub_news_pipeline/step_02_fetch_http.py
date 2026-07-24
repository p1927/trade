"""Pipeline step 02 — HTTP article body fetch."""

from __future__ import annotations

import time
from typing import Any

from trade_integrations.dataflows.index_research.hub_news_pipeline.pipeline_context import (
    RefPipelineContext,
    StepResult,
)
from trade_integrations.dataflows.index_research.hub_news_pipeline.step_03_datetime_normalize import (
    extract_published_meta_from_html,
)

STEP_ID = "step_02_fetch_http"
_MIN_BODY_LEN = 120


def _snippet_text(ctx: RefPipelineContext) -> str:
    title = str(ctx.ref.get("title") or "").strip()
    summary = str(ctx.ref.get("summary") or "").strip()
    if title and summary:
        return f"{title}\n\n{summary}"
    return title or summary


def _apply_fetch_result(
    ctx: RefPipelineContext,
    *,
    body: str | None,
    html_text: str = "",
    fetch_method: str = "http",
    duration_ms: float,
) -> tuple[RefPipelineContext, StepResult]:
    if html_text:
        if "<" in html_text[:48].lower():
            meta = extract_published_meta_from_html(html_text)
        else:
            meta = html_text.strip()
        if meta:
            ctx.ref["_raw_html_meta_published"] = meta

    if body and len(body.strip()) >= _MIN_BODY_LEN:
        ctx.article_body = body.strip()
        ctx.enrichment_mode = "full"
        ctx.fetch_status = "ok"
        ctx.fetch_method = fetch_method
        result = StepResult(
            step_id=STEP_ID,
            status="ok",
            duration_ms=duration_ms,
            detail={
                "enrichment_mode": "full",
                "body_len": len(ctx.article_body),
                "fetch_method": fetch_method,
                "meta_captured": bool(ctx.ref.get("_raw_html_meta_published")),
            },
        )
        ctx.record_step(result)
        return ctx, result

    ctx.enrichment_mode = "snippet_fallback"
    ctx.fetch_status = "failed" if body is None else "body_too_short"
    ctx.fetch_method = fetch_method
    ctx.article_body = _snippet_text(ctx)
    result = StepResult(
        step_id=STEP_ID,
        status="ok",
        duration_ms=duration_ms,
        detail={
            "enrichment_mode": "snippet_fallback",
            "fetch_status": ctx.fetch_status,
            "meta_captured": bool(ctx.ref.get("_raw_html_meta_published")),
        },
    )
    ctx.record_step(result)
    return ctx, result


def _default_fetch(url: str) -> tuple[str | None, str]:
    from trade_integrations.dataflows.article_body import fetch_article_body_with_html

    return fetch_article_body_with_html(url)


def run_step_02_fetch_http(
    ctx: RefPipelineContext,
    *,
    fetch_fn: Any | None = None,
    min_summary_len: int | None = None,
    **_: Any,
) -> tuple[RefPipelineContext, StepResult]:
    """Fetch article via HTTP (trusted domains). On failure, set snippet_fallback — do not stop."""
    started = time.perf_counter()
    if not ctx.should_continue:
        result = StepResult(
            step_id=STEP_ID,
            status="skipped",
            detail={"reason": ctx.discard_reason or "pipeline_stopped"},
        )
        ctx.record_step(result)
        return ctx, result

    from trade_integrations.dataflows.article_body import min_summary_len_for_fetch

    url = str(ctx.ref.get("url") or "").strip()
    summary = str(ctx.ref.get("summary") or "").strip()
    threshold = min_summary_len_for_fetch() if min_summary_len is None else min_summary_len

    if not url:
        ctx.enrichment_mode = "snippet_fallback"
        ctx.fetch_status = "no_url"
        ctx.fetch_method = "none"
        ctx.article_body = _snippet_text(ctx)
        duration_ms = (time.perf_counter() - started) * 1000
        result = StepResult(
            step_id=STEP_ID,
            status="ok",
            duration_ms=duration_ms,
            detail={"enrichment_mode": ctx.enrichment_mode, "reason": "missing_url"},
        )
        ctx.record_step(result)
        return ctx, result

    if len(summary) >= threshold:
        ctx.enrichment_mode = "snippet_fallback"
        ctx.fetch_status = "skipped_summary_sufficient"
        ctx.fetch_method = "none"
        ctx.article_body = _snippet_text(ctx)
        duration_ms = (time.perf_counter() - started) * 1000
        result = StepResult(
            step_id=STEP_ID,
            status="ok",
            duration_ms=duration_ms,
            detail={
                "enrichment_mode": ctx.enrichment_mode,
                "reason": "summary_above_threshold",
                "summary_len": len(summary),
            },
        )
        ctx.record_step(result)
        return ctx, result

    fetch = fetch_fn or _default_fetch
    raw = fetch(url)
    duration_ms = (time.perf_counter() - started) * 1000

    if isinstance(raw, tuple):
        body, html_text = raw[0], raw[1] if len(raw) > 1 else ""
    else:
        body, html_text = raw, ""

    return _apply_fetch_result(
        ctx,
        body=body,
        html_text=str(html_text or ""),
        fetch_method="http",
        duration_ms=duration_ms,
    )
