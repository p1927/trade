"""Tests for news event scenario draft + quant run."""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from trade_integrations.dataflows.index_research.news_event_scenarios import (
    load_news_event_scenario,
    run_news_event_scenario,
    save_news_scenario_draft,
)
from trade_integrations.dataflows.index_research.news_scenario_tools import (
    tool_get_pipeline_snapshot,
    tool_save_news_scenario_draft,
)


@pytest.fixture
def hub_with_index(tmp_path, monkeypatch):
    monkeypatch.setenv("TRADE_STACK_HUB_DIR", str(tmp_path))
    as_of = datetime(2026, 7, 17, 10, 30, 0, tzinfo=timezone.utc)
    hub_dir = tmp_path / "NIFTY" / "index_research"
    hub_dir.mkdir(parents=True)
    payload = {
        "ticker": "NIFTY",
        "as_of": as_of.isoformat(),
        "spot": 24500.0,
        "horizon": {"days": 14},
        "prediction": {
            "expected_return_pct": 1.0,
            "bottom_up_return_pct": 0.3,
            "view": "bullish",
        },
        "global_factors": [
            {"factor": "usd_inr", "value": 83.5},
            {"factor": "oil_brent", "value": 82.0},
            {"factor": "india_vix", "value": 14.0},
            {"factor": "index_sentiment", "value": 0.1},
        ],
        "factor_explanation": {"contributors": [{"factor": "usd_inr", "contribution_pct": 0.2}]},
        "news_impact": {"items": [{"title": "Oil rises", "publish_date": "2026-08-05"}]},
    }
    (hub_dir / "latest.json").write_text(json.dumps(payload), encoding="utf-8")
    return as_of.isoformat()


@pytest.mark.unit
def test_save_and_run_scenario(hub_with_index):
    as_of = hub_with_index
    draft = save_news_scenario_draft(
        ticker="NIFTY",
        pipeline_as_of=as_of,
        draft={
            "date_range": {"start": "2026-08-01", "end": "2026-08-15"},
            "event": {"source": "custom", "title": "Geopolitical tension", "topic_tags": ["war"]},
            "outcomes": [
                {
                    "id": "escalation",
                    "label": "Escalation",
                    "intensity": "high",
                    "primary_factor": "oil_brent",
                    "factor_overrides": {"oil_brent": "+10%", "india_vix": "+15%"},
                },
                {
                    "id": "calm",
                    "label": "De-escalation",
                    "intensity": "low",
                    "primary_factor": "oil_brent",
                    "factor_overrides": {"oil_brent": "-5%"},
                },
            ],
        },
    )
    draft_id = draft["draft_id"]
    product = run_news_event_scenario(
        ticker="NIFTY",
        pipeline_as_of=as_of,
        draft_id=draft_id,
    )
    assert product["scenario_id"]
    assert len(product["outcomes"]) == 2
    assert product["baseline"]["spot"] == 24500.0
    loaded = load_news_event_scenario("NIFTY", product["scenario_id"])
    assert loaded is not None
    assert loaded["draft_id"] == draft_id


@pytest.mark.unit
def test_tool_get_pipeline_snapshot(hub_with_index):
    raw = tool_get_pipeline_snapshot("NIFTY", hub_with_index)
    payload = json.loads(raw)
    assert payload["status"] == "ok"
    assert payload["snapshot"]["spot"] == 24500.0


@pytest.mark.unit
def test_run_requires_date_range(hub_with_index):
    as_of = hub_with_index
    draft = save_news_scenario_draft(
        ticker="NIFTY",
        pipeline_as_of=as_of,
        draft={
            "event": {"title": "Test"},
            "outcomes": [{"id": "a", "label": "A", "intensity": "medium", "primary_factor": "oil_brent"}],
        },
    )
    with pytest.raises(Exception) as exc:
        run_news_event_scenario(ticker="NIFTY", pipeline_as_of=as_of, draft_id=draft["draft_id"])
    assert getattr(exc.value, "code", "") == "missing_date_range" or "missing_date_range" in str(exc.value)


@pytest.mark.unit
def test_date_range_max_90_days(hub_with_index):
    from trade_integrations.dataflows.index_research.news_event_scenarios import (
        DateRangeTooWideError,
        validate_scenario_date_range,
    )

    with pytest.raises(DateRangeTooWideError):
        validate_scenario_date_range({"start": "2026-01-01", "end": "2026-06-01"}, require_complete=True)


@pytest.mark.unit
def test_tool_save_draft_json(hub_with_index):
    raw = tool_save_news_scenario_draft(
        "NIFTY",
        hub_with_index,
        json.dumps(
            {
                "event": {"title": "Test"},
                "outcomes": [{"id": "a", "label": "A", "intensity": "medium"}],
            }
        ),
    )
    payload = json.loads(raw)
    assert payload["status"] == "ok"
    assert payload["draft"]["draft_id"]
