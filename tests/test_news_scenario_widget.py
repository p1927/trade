"""Tests for news scenario widget payload."""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from trade_integrations.dataflows.index_research.news_event_scenarios import (
    run_news_event_scenario,
    save_news_scenario_draft,
)
from trade_integrations.dataflows.index_research.news_scenario_widget import build_news_scenario_widget


@pytest.fixture
def scenario_product(tmp_path, monkeypatch):
    monkeypatch.setenv("TRADE_STACK_HUB_DIR", str(tmp_path))
    as_of = datetime(2026, 7, 17, 10, 30, 0, tzinfo=timezone.utc)
    hub_dir = tmp_path / "NIFTY" / "index_research"
    hub_dir.mkdir(parents=True)
    payload = {
        "ticker": "NIFTY",
        "as_of": as_of.isoformat(),
        "spot": 24500.0,
        "horizon": {"days": 14},
        "prediction": {"expected_return_pct": 1.0, "bottom_up_return_pct": 0.3},
        "global_factors": [
            {"factor": "oil_brent", "value": 82.0},
            {"factor": "india_vix", "value": 14.0},
            {"factor": "index_sentiment", "value": 0.1},
        ],
    }
    (hub_dir / "latest.json").write_text(json.dumps(payload), encoding="utf-8")
    as_of_str = as_of.isoformat()
    draft = save_news_scenario_draft(
        ticker="NIFTY",
        pipeline_as_of=as_of_str,
        draft={
            "date_range": {"start": "2026-08-01", "end": "2026-08-15"},
            "event": {"title": "Test event"},
            "outcomes": [
                {
                    "id": "up",
                    "label": "Up",
                    "intensity": "medium",
                    "primary_factor": "index_sentiment",
                    "factor_overrides": {"index_sentiment": "+5%"},
                }
            ],
        },
    )
    product = run_news_event_scenario(
        ticker="NIFTY",
        pipeline_as_of=as_of_str,
        draft_id=draft["draft_id"],
    )
    return product


@pytest.mark.unit
def test_news_scenario_widget_shape(scenario_product):
    product = scenario_product
    widget = build_news_scenario_widget(
        "NIFTY",
        scenario_id=product["scenario_id"],
        selected_outcome_id="up",
    )
    assert widget["type"] == "trade_plan.widget"
    assert widget["widget_kind"] == "news_event_scenario"
    assert widget["underlying"] == "NIFTY"
    assert widget["outcomes"]
    assert widget["selected_outcome_id"] == "up"
