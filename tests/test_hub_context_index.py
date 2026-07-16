"""Tests for index research context in hub_context."""

from __future__ import annotations

import pytest


@pytest.mark.unit
def test_index_context_includes_factors_and_widget_mandate():
    from trade_integrations.bridge.hub_context import format_research_context_for_agent

    index_artifact = {
        "underlying": "NIFTY",
        "asset_type": "index",
        "plan_status": "ready",
        "spot": 24500.0,
        "horizon": {"days": 14, "label": "B"},
        "prediction": {
            "view": "bullish",
            "expected_return_pct": 1.2,
            "range": {"low": 24200.0, "high": 24900.0},
        },
        "regime": {"label": "risk_on"},
        "top_factors": [
            {
                "factor": "usd_inr",
                "share_of_macro": 35.0,
                "contribution_index_pts": 12.5,
            }
        ],
        "scenarios": [
            {
                "event": "RBI",
                "outcome": "hold",
                "probability": 0.6,
                "index_range": "24400-24600",
            }
        ],
        "accuracy": {"direction_hit_rate": 0.62},
    }
    context = format_research_context_for_agent(
        None,
        index_artifact=index_artifact,
        widget_intent="index_outlook",
    )

    assert "[index_research_context]" in context
    assert "index_prediction: view=bullish" in context
    assert "top_factor_contributors:" in context
    assert "usd_inr" in context
    assert "get_index_trade_widget(ticker)" in context


@pytest.mark.unit
def test_combined_options_and_index_context():
    from trade_integrations.bridge.hub_context import format_research_context_for_agent

    options_artifact = {
        "underlying": "NIFTY",
        "asset_type": "options",
        "plan_status": "ready",
        "ranked_strategies": [{"name": "Iron condor", "tier": "A", "score": 0.82}],
        "recommended": {"name": "Iron condor", "legs": [{"side": "SELL", "strike": 24000}]},
    }
    index_artifact = {
        "underlying": "NIFTY",
        "asset_type": "index",
        "plan_status": "ready",
        "prediction": {"view": "neutral"},
    }
    context = format_research_context_for_agent(options_artifact, index_artifact=index_artifact)

    assert "[research_context]" in context
    assert "[index_research_context]" in context
    assert "get_options_trade_widget" in context
    assert "get_index_trade_widget" in context
