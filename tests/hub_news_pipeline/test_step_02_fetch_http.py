"""Tests for hub news pipeline step 02 — HTTP fetch."""

from __future__ import annotations

from trade_integrations.dataflows.index_research.hub_news_pipeline.pipeline_context import (
    RefPipelineContext,
)
from trade_integrations.dataflows.index_research.hub_news_pipeline.step_02_fetch_http import (
    run_step_02_fetch_http,
)


def test_step_02_full_mode_on_successful_fetch():
    ctx = RefPipelineContext(
        ref={
            "title": "NIFTY update",
            "summary": "Short",
            "url": "https://www.livemint.com/markets/article",
        },
    )

    def _fetch(_url):
        return ("A" * 200, '<meta property="article:published_time" content="2026-03-15T09:00:00+05:30"/>')

    ctx, result = run_step_02_fetch_http(ctx, fetch_fn=_fetch, min_summary_len=400)
    assert result.status == "ok"
    assert ctx.enrichment_mode == "full"
    assert ctx.fetch_status == "ok"
    assert len(ctx.article_body) == 200
    assert ctx.ref.get("_raw_html_meta_published")
    assert ctx.should_continue is True


def test_step_02_snippet_fallback_on_fetch_fail():
    ctx = RefPipelineContext(
        ref={
            "title": "NIFTY update",
            "summary": "Thin snippet",
            "url": "https://www.livemint.com/markets/article",
        },
    )

    def _fetch(_url):
        return None

    ctx, result = run_step_02_fetch_http(ctx, fetch_fn=_fetch, min_summary_len=400)
    assert result.status == "ok"
    assert ctx.enrichment_mode == "snippet_fallback"
    assert ctx.fetch_status == "failed"
    assert "NIFTY update" in ctx.article_body
    assert ctx.should_continue is True


def test_step_02_skips_fetch_when_summary_long_enough():
    ctx = RefPipelineContext(
        ref={
            "title": "NIFTY",
            "summary": "x" * 500,
            "url": "https://www.livemint.com/markets/article",
        },
    )

    def _fetch(_url):
        raise AssertionError("fetch should not run")

    ctx, result = run_step_02_fetch_http(ctx, fetch_fn=_fetch, min_summary_len=400)
    assert result.status == "ok"
    assert ctx.enrichment_mode == "snippet_fallback"
    assert ctx.fetch_status == "skipped_summary_sufficient"


def test_step_02_skipped_when_pipeline_stopped():
    ctx = RefPipelineContext(ref={"title": "x"}, should_continue=False, discard_reason="test")
    ctx, result = run_step_02_fetch_http(ctx)
    assert result.status == "skipped"
