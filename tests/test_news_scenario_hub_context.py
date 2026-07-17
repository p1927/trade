"""Tests for news scenario hub context block."""

from __future__ import annotations

from trade_integrations.bridge.hub_context import (
    format_news_scenario_context,
    format_research_context_for_agent,
)


def test_format_news_scenario_context():
    block = format_news_scenario_context(
        {
            "session_kind": "news_scenario_advisor",
            "pipeline_as_of": "2026-07-17T10:30:00+00:00",
            "pipeline_ticker": "NIFTY",
            "date_range": {"start": "2026-08-01", "end": "2026-08-15"},
        }
    )
    assert "[news_scenario_context]" in block
    assert "pipeline_as_of" in block
    assert "save_news_scenario_draft" in block


def test_format_research_context_includes_news_block():
    ctx = format_research_context_for_agent(
        None,
        index_artifact={"ticker": "NIFTY", "spot": 24500, "prediction": {"view": "bullish"}},
        session_config={"session_kind": "news_scenario_advisor", "pipeline_as_of": "2026-07-17T10:30:00+00:00"},
    )
    assert "[index_research_context]" in ctx
    assert "[news_scenario_context]" in ctx
