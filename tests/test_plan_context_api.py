"""Tests for GET /trade/plan-context/{ticker}."""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

ROOT = Path(__file__).resolve().parents[1]
AGENT_SRC = ROOT / "vibetrading" / "agent"
INTEGRATIONS = ROOT / "integrations"
for path in (AGENT_SRC, INTEGRATIONS):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from src.api.trade_routes import get_plan_context  # noqa: E402
from trade_integrations.monitor.plan_staleness import StalenessReport  # noqa: E402


@pytest.mark.unit
def test_plan_context_monitor_disabled(monkeypatch):
    monkeypatch.setenv("OPTIONS_REALTIME_MONITOR_ENABLED", "false")

    response = get_plan_context("NIFTY", _auth=None)

    assert response == {"monitor_enabled": False}


@pytest.mark.unit
def test_plan_context_monitor_enabled(monkeypatch):
    monkeypatch.setenv("OPTIONS_REALTIME_MONITOR_ENABLED", "true")
    report = StalenessReport(
        ticker="NIFTY",
        status="stale",
        as_of=datetime(2026, 7, 16, 8, 0, tzinfo=timezone.utc),
        live_spot=24500.0,
        plan_spot=24000.0,
        spot_drift_pct=2.08,
        age_minutes=45.0,
        reasons=["spot_drift"],
        suggested_action="refresh",
    )
    fake_service = MagicMock()
    fake_service.evaluate_ticker.return_value = report

    with (
        patch("trade_integrations.monitor.service.MonitorService", return_value=fake_service),
        patch("src.api.trade_routes._material_news_count", return_value=2),
        patch("src.api.trade_routes._has_open_plan_position", return_value=True),
    ):
        response = get_plan_context("nifty", _auth=None)

    assert response["monitor_enabled"] is True
    assert response["ticker"] == "NIFTY"
    assert response["staleness"]["status"] == "stale"
    assert response["staleness"]["spot_drift_pct"] == pytest.approx(2.08)
    assert response["live_context"]["spot"] == pytest.approx(24500.0)
    assert response["live_context"]["plan_spot"] == pytest.approx(24000.0)
    assert response["material_news_count"] == 2
    assert response["open_position"] is True
