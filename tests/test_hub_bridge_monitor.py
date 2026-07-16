"""Tests for gated plan monitor integration in hub_bridge prefetch."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

ROOT = Path(__file__).resolve().parents[1]
AGENT_SRC = ROOT / "vibetrading" / "agent"
INTEGRATIONS = ROOT / "integrations"
for path in (AGENT_SRC, INTEGRATIONS):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from src.trade.hub_bridge import (  # noqa: E402
    _maybe_evaluate_plan_staleness,
    prefetch_research_for_message,
)
from trade_integrations.monitor.plan_staleness import StalenessReport  # noqa: E402


class _FakeEventBus:
    def __init__(self) -> None:
        self.events: list[tuple[str, str, dict]] = []

    def emit(self, session_id: str, event_type: str, data: dict) -> None:
        self.events.append((session_id, event_type, data))


@pytest.mark.unit
def test_staleness_skipped_when_monitor_disabled(monkeypatch):
    monkeypatch.setenv("OPTIONS_REALTIME_MONITOR_ENABLED", "false")
    artifact = {"ticker": "NIFTY", "plan_status": "ready"}
    bus = _FakeEventBus()

    _maybe_evaluate_plan_staleness(artifact, "NIFTY", "options", bus, "sess-1")

    assert "staleness" not in artifact
    assert bus.events == []


@pytest.mark.unit
def test_staleness_attached_and_sse_emitted_when_stale(monkeypatch):
    monkeypatch.setenv("OPTIONS_REALTIME_MONITOR_ENABLED", "true")
    report = StalenessReport(
        ticker="NIFTY",
        status="stale",
        as_of=None,
        live_spot=25000.0,
        plan_spot=24500.0,
        spot_drift_pct=2.04,
        age_minutes=12.0,
        reasons=["spot_drift"],
        suggested_action="re_recommend",
    )
    artifact = {"ticker": "NIFTY", "plan_status": "ready"}
    bus = _FakeEventBus()

    with patch("trade_integrations.monitor.service.MonitorService") as mock_cls:
        mock_cls.is_enabled.return_value = True
        mock_cls.return_value.evaluate_ticker.return_value = report
        _maybe_evaluate_plan_staleness(artifact, "NIFTY", "options", bus, "sess-2")

    assert artifact["staleness"]["status"] == "stale"
    assert artifact["staleness"]["reasons"] == ["spot_drift"]
    assert len(bus.events) == 1
    assert bus.events[0][1] == "plan.stale"
    assert bus.events[0][2]["ticker"] == "NIFTY"
    assert bus.events[0][2]["status"] == "stale"


@pytest.mark.unit
def test_fresh_report_attaches_staleness_without_sse(monkeypatch):
    monkeypatch.setenv("OPTIONS_REALTIME_MONITOR_ENABLED", "true")
    report = StalenessReport(
        ticker="NIFTY",
        status="fresh",
        as_of=None,
        live_spot=24510.0,
        plan_spot=24500.0,
        spot_drift_pct=0.04,
        age_minutes=3.0,
        reasons=["within_thresholds"],
        suggested_action="none",
    )
    artifact = {"ticker": "NIFTY", "plan_status": "ready"}
    bus = _FakeEventBus()

    with patch("trade_integrations.monitor.service.MonitorService") as mock_cls:
        mock_cls.is_enabled.return_value = True
        mock_cls.return_value.evaluate_ticker.return_value = report
        _maybe_evaluate_plan_staleness(artifact, "NIFTY", "options", bus, "sess-3")

    assert artifact["staleness"]["status"] == "fresh"
    assert bus.events == []


@pytest.mark.unit
def test_prefetch_research_does_not_call_monitor_when_disabled(monkeypatch):
    monkeypatch.setenv("OPTIONS_REALTIME_MONITOR_ENABLED", "false")
    artifact = {"ticker": "NIFTY", "plan_status": "ready", "asset_type": "options"}
    bus = _FakeEventBus()

    with (
        patch("src.trade.hub_bridge.extract_primary_ticker", return_value="NIFTY"),
        patch("src.trade.hub_bridge.infer_asset_type", return_value="options"),
        patch("src.trade.hub_bridge.prefetch_hub_plan", return_value=artifact),
        patch("src.trade.hub_bridge._options_auto_widget_enabled", return_value=False),
        patch(
            "trade_integrations.monitor.service.MonitorService.evaluate_ticker",
            side_effect=AssertionError("evaluate_ticker must not run when monitor disabled"),
        ),
    ):
        context = prefetch_research_for_message("sess-4", "NIFTY options", bus)

    assert "staleness" not in artifact
    assert any(e[1] == "research.artifact" for e in bus.events)
    assert not any(e[1] == "plan.stale" for e in bus.events)
    assert "[research_context]" in context


@pytest.mark.unit
def test_hub_context_includes_staleness_lines():
    from trade_integrations.bridge.hub_context import format_research_context_for_agent

    artifact = {
        "underlying": "NIFTY",
        "asset_type": "options",
        "plan_status": "ready",
        "staleness": {
            "status": "stale",
            "reasons": ["spot_drift", "age_exceeded"],
            "suggested_action": "re_recommend",
        },
    }
    context = format_research_context_for_agent(artifact)

    assert "staleness_status: stale" in context
    assert "staleness_reasons: spot_drift, age_exceeded" in context
    assert "suggested_action: re_recommend" in context
    assert "get_options_trade_widget(ticker, refresh=true)" in context
