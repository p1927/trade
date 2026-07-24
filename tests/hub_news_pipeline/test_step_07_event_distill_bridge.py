"""Tests for hub news pipeline step 07 — event distill bridge."""

from __future__ import annotations

from trade_integrations.dataflows.index_research.hub_news_pipeline.pipeline_context import (
    RefPipelineContext,
)
from trade_integrations.dataflows.index_research.hub_news_pipeline.step_07_event_distill_bridge import (
    format_enrichment_distill_block,
    run_step_07_event_distill_bridge,
)


def test_format_enrichment_distill_block():
    block = format_enrichment_distill_block(
        {
            "cause_indicators": [{"factor": "fii_net_5d", "mechanism": "outflows", "direction_hint": "bearish"}],
            "future_events": [{"event": "RBI MPC", "expected_date": "2026-03-22"}],
        }
    )
    assert "fii_net_5d" in block
    assert "RBI MPC" in block


def test_step_07_attaches_structured_enrichment():
    ctx = RefPipelineContext(ref={"title": "NIFTY"})
    ctx.article_enrichment = {
        "cause_indicators": [{"factor": "oil_brent", "mechanism": "crude spike"}],
        "future_events": [],
        "article_opinions": [{"kind": "price_prediction", "text": "25000"}],
    }
    ctx, result = run_step_07_event_distill_bridge(ctx)
    assert result.status == "ok"
    assert ctx.ref["pipeline_distill_hints"]
    assert len(ctx.ref["structured_enrichment"]["cause_indicators"]) == 1
    assert len(ctx.ref["structured_enrichment"]["article_opinions"]) == 1
