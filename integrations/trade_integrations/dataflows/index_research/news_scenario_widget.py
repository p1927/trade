"""Build trade_plan.widget payload for news event scenarios."""

from __future__ import annotations

import uuid
from typing import Any

from trade_integrations.dataflows.index_research.news_event_scenarios import load_news_event_scenario
from trade_integrations.trade_widgets.presentability import apply_widget_metadata


def build_news_scenario_widget(
    ticker: str = "NIFTY",
    *,
    scenario_id: str,
    selected_outcome_id: str | None = None,
    widget_intent: str | None = None,
) -> dict[str, Any]:
    """Build Vibe widget from a saved NewsEventScenarioDoc."""
    sym = ticker.strip().upper()
    scenario = load_news_event_scenario(sym, scenario_id)
    if not scenario:
        raise FileNotFoundError(f"Scenario {scenario_id} not found for {sym}")

    baseline = scenario.get("baseline") or {}
    outcomes = scenario.get("outcomes") or []
    widget_id = f"ns_{sym}_{uuid.uuid4().hex[:12]}"

    payload: dict[str, Any] = {
        "type": "trade_plan.widget",
        "widget_kind": "news_event_scenario",
        "widget_id": widget_id,
        "asset_type": "index",
        "underlying": sym,
        "instrument_type": "index",
        "market": "IN",
        "spot": baseline.get("spot"),
        "date_range": scenario.get("date_range"),
        "event": scenario.get("event"),
        "baseline": baseline,
        "outcomes": outcomes,
        "fan_band": scenario.get("fan_band"),
        "selected_outcome_id": selected_outcome_id,
        "scenario_id": scenario_id,
        "pipeline_as_of": scenario.get("pipeline_as_of"),
        "plan_status": "ready",
    }
    return apply_widget_metadata(payload, widget_intent)
