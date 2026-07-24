"""Tests for hub news pipeline step 02b — Crawl4AI fetch."""

from __future__ import annotations

from dataclasses import dataclass

from trade_integrations.dataflows.index_research.hub_news_pipeline.pipeline_context import (
    RefPipelineContext,
)
from trade_integrations.dataflows.index_research.hub_news_pipeline.step_02b_fetch_crawl4ai import (
    run_step_02b_fetch_crawl4ai,
)


@dataclass
class _CrawlPage:
    url: str
    success: bool
    markdown: str = ""
    metadata: dict | None = None


def test_step_02b_skipped_when_disabled(monkeypatch):
    monkeypatch.setenv("HUB_NEWS_CRAWL4AI_ENABLED", "0")
    ctx = RefPipelineContext(
        ref={"url": "https://example.com/a"},
        enrichment_mode="snippet_fallback",
        fetch_status="failed",
    )
    ctx, result = run_step_02b_fetch_crawl4ai(ctx)
    assert result.status == "skipped"
    assert result.detail.get("reason") == "crawl4ai_disabled"


def test_step_02b_skipped_when_http_body_sufficient(monkeypatch):
    monkeypatch.setenv("HUB_NEWS_CRAWL4AI_ENABLED", "1")
    ctx = RefPipelineContext(
        ref={"url": "https://example.com/a"},
        enrichment_mode="full",
        article_body="x" * 200,
    )

    def _crawl(_urls):
        raise AssertionError("crawl should not run")

    ctx, result = run_step_02b_fetch_crawl4ai(ctx, crawl_fn=_crawl)
    assert result.status == "ok"
    assert result.detail.get("skipped_crawl") is True


def test_step_02b_upgrades_body_on_success(monkeypatch):
    monkeypatch.setenv("HUB_NEWS_CRAWL4AI_ENABLED", "1")
    ctx = RefPipelineContext(
        ref={"url": "https://example.com/a", "title": "NIFTY", "summary": "down"},
        enrichment_mode="snippet_fallback",
        fetch_status="failed",
        article_body="NIFTY\ndown",
    )

    def _crawl(_urls):
        return [_CrawlPage(url=_urls[0], success=True, markdown="Crawl body " * 30)]

    ctx, result = run_step_02b_fetch_crawl4ai(ctx, crawl_fn=_crawl)
    assert result.status == "ok"
    assert ctx.enrichment_mode == "full"
    assert ctx.fetch_method == "crawl4ai"
    assert len(ctx.article_body) >= 120


def test_step_02b_keeps_snippet_fallback_on_crawl_fail(monkeypatch):
    monkeypatch.setenv("HUB_NEWS_CRAWL4AI_ENABLED", "1")
    ctx = RefPipelineContext(
        ref={"url": "https://example.com/a", "title": "NIFTY", "summary": "down"},
        enrichment_mode="snippet_fallback",
        fetch_status="failed",
        article_body="NIFTY\ndown",
    )

    def _crawl(_urls):
        return [_CrawlPage(url=_urls[0], success=False, markdown="")]

    ctx, result = run_step_02b_fetch_crawl4ai(ctx, crawl_fn=_crawl)
    assert result.status == "ok"
    assert ctx.enrichment_mode == "snippet_fallback"
    assert "NIFTY" in ctx.article_body
