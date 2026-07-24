"""Tests for hub news pipeline step 05 — claims bridge."""

from __future__ import annotations

import json

from trade_integrations.dataflows.index_research.hub_news_pipeline.pipeline_context import (
    RefPipelineContext,
)
from trade_integrations.dataflows.index_research.hub_news_pipeline.step_05_claims_bridge import (
    run_step_05_claims_bridge,
)


def test_step_05_attaches_enrichment_and_claims():
    ctx = RefPipelineContext(
        ref={
            "title": "NIFTY falls 1% on FII selling",
            "summary": "FII sold 2000 crore",
        },
    )
    ctx.article_enrichment = {
        "distilled_summary": "FII outflows weigh on NIFTY",
        "cause_indicators": [{"factor": "fii_net_5d", "mechanism": "outflows"}],
        "future_events": [],
    }

    ctx, result = run_step_05_claims_bridge(ctx)
    assert result.status == "ok"
    assert ctx.ref["summary"] == "FII outflows weigh on NIFTY"
    assert ctx.ref.get("article_enrichment")
    assert isinstance(ctx.ref.get("extracted_claims"), list)
