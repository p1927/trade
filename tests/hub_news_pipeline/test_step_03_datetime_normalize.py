"""Tests for hub news pipeline step 03 — datetime normalize."""

from __future__ import annotations

from trade_integrations.dataflows.index_research.hub_news_pipeline.pipeline_context import (
    RefPipelineContext,
)
from trade_integrations.dataflows.index_research.hub_news_pipeline.step_03_datetime_normalize import (
    extract_published_meta_from_html,
    resolve_published_at,
    run_step_03_datetime_normalize,
)


def test_resolve_prefers_meta_over_rss():
    published, day, conflict, source = resolve_published_at(
        ref_published_at="2026-03-10T09:00:00+00:00",
        meta_published_at="2026-03-15T10:30:00+05:30",
    )
    assert day == "2026-03-15"
    assert source == "article_meta"
    assert conflict is True


def test_resolve_rss_date_only_gets_market_open_ist():
    published, day, conflict, source = resolve_published_at(
        ref_published_at="2026-03-15",
        meta_published_at="",
    )
    assert day == "2026-03-15"
    assert source == "rss"
    assert conflict is False
    assert "+05:30" in published


def test_extract_meta_from_html():
    html = '<meta property="article:published_time" content="2026-04-01T08:00:00+05:30">'
    assert extract_published_meta_from_html(html) == "2026-04-01T08:00:00+05:30"


def test_step_03_updates_context_and_ref():
    ctx = RefPipelineContext(
        ref={
            "published_at": "2026-03-10",
            "article_meta_published_at": "2026-03-16T11:00:00+05:30",
        },
    )
    ctx, result = run_step_03_datetime_normalize(ctx)
    assert result.status == "ok"
    assert ctx.publish_day == "2026-03-16"
    assert ctx.date_conflict is True
    assert ctx.ref["publish_day"] == "2026-03-16"
