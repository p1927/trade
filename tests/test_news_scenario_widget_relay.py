"""Widget SSE relay for news scenario widgets."""

from __future__ import annotations

import json
from types import SimpleNamespace

from src.api.trade_routes import (
    load_trade_widget,
    trade_plan_widget_frame_from_tool_result,
    trade_widget_dir,
)
from trade_integrations.dataflows.index_research.news_scenario_widget import build_news_scenario_widget
from trade_integrations.trade_widgets.store import persist_trade_widget


def test_news_scenario_widget_persist_and_relay(tmp_path, monkeypatch):
    monkeypatch.setenv("TRADE_STACK_HUB_DIR", str(tmp_path))
    widget_dir = tmp_path / "widgets"
    widget_dir.mkdir()
    monkeypatch.setattr(
        "trade_integrations.trade_widgets.store.trade_widget_dir",
        lambda: widget_dir,
    )
    monkeypatch.setattr(
        "src.api.trade_routes.trade_widget_dir",
        lambda: widget_dir,
    )

    scenario = {
        "scenario_id": "scen123",
        "pipeline_as_of": "2026-07-17T10:30:00+00:00",
        "ticker": "NIFTY",
        "date_range": {"start": "2026-08-01", "end": "2026-08-15"},
        "event": {"title": "Test"},
        "baseline": {"spot": 24500, "expected_return_pct": 1.0, "path": []},
        "outcomes": [],
        "fan_band": {},
    }
    hub = tmp_path / "NIFTY" / "news_event_scenarios" / "history"
    hub.mkdir(parents=True)
    (hub / "scen123.json").write_text(json.dumps(scenario), encoding="utf-8")

    widget = build_news_scenario_widget("NIFTY", scenario_id="scen123")
    assert widget["widget_id"].startswith("ns_NIFTY_")
    persist_trade_widget(widget)
    assert load_trade_widget(widget["widget_id"]) is not None

    preview = json.dumps(widget)[:200]
    event = SimpleNamespace(
        event_type="tool_result",
        session_id="sess1",
        data={
            "tool": "mcp_openalgo_get_news_scenario_widget",
            "status": "ok",
            "preview": preview,
        },
    )
    frame = trade_plan_widget_frame_from_tool_result(event)
    assert frame is not None
    assert "trade_plan.widget" in frame
    assert widget["widget_id"] in frame
