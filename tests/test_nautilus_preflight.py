"""Tests for execution pre-flight checks."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

ROOT = Path(__file__).resolve().parents[1]
INTEGRATIONS = ROOT / "integrations"
if str(INTEGRATIONS) not in sys.path:
    sys.path.insert(0, str(INTEGRATIONS))

from nautilus_openalgo_bridge.models import ExecutionIntent, ExecutionLeg, IntentAction  # noqa: E402
from nautilus_openalgo_bridge.preflight import run_preflight  # noqa: E402


def test_preflight_exit_blocked_for_autonomous_outside_window():
    client = MagicMock()
    client.ensure_analyzer_mode.return_value = True
    intent = ExecutionIntent(action=IntentAction.EXIT, agent_id="aa_x", rationale="flatten")
    with patch("nautilus_openalgo_bridge.preflight.is_bridge_exit_window_open", return_value=False):
        result = run_preflight(intent, client)
    assert result["blocked"] is True
    assert result["reason"] == "outside_exit_window"


def test_preflight_exit_analyzer_bypass_only_without_agent_id(monkeypatch):
    monkeypatch.setenv("ANALYZER", "1")
    client = MagicMock()
    client.ensure_analyzer_mode.return_value = True
    intent = ExecutionIntent(action=IntentAction.EXIT, agent_id="", rationale="flatten")
    with patch("nautilus_openalgo_bridge.preflight.is_bridge_exit_window_open", return_value=False):
        result = run_preflight(intent, client)
    assert result["blocked"] is False
    assert result["checks"].get("paper_exit_analyzer_bypass") is True


def test_preflight_exit_blocked_outside_window_when_not_paper():
    client = MagicMock()
    client.ensure_analyzer_mode.return_value = True
    intent = ExecutionIntent(action=IntentAction.EXIT, agent_id="aa_x", rationale="flatten")
    with patch("nautilus_openalgo_bridge.preflight.get_bridge_config") as mock_cfg, patch(
        "nautilus_openalgo_bridge.preflight.is_bridge_exit_window_open", return_value=False
    ):
        mock_cfg.return_value.paper_only = False
        result = run_preflight(intent, client)
    assert result["blocked"] is True
    assert result["reason"] == "outside_exit_window"


def test_preflight_enter_checks_margin():
    client = MagicMock()
    client.ensure_analyzer_mode.return_value = True
    client.calculate_margin.return_value = 12500.0
    intent = ExecutionIntent(
        action=IntentAction.ENTER,
        agent_id="aa_x",
        rationale="go",
        legs=[
            ExecutionLeg(symbol="NIFTY24JUL24500CE", exchange="NFO", action="SELL", quantity=50),
        ],
    )
    with patch("nautilus_openalgo_bridge.preflight.is_bridge_market_open", return_value=True):
        result = run_preflight(intent, client)
    assert result["blocked"] is False
    assert result["checks"]["margin_inr"] == 12500.0


def test_preflight_enter_blocked_when_margin_unavailable():
    client = MagicMock()
    client.ensure_analyzer_mode.return_value = True
    client.calculate_margin.return_value = None
    intent = ExecutionIntent(
        action=IntentAction.ENTER,
        agent_id="aa_x",
        rationale="go",
        legs=[ExecutionLeg(symbol="X", exchange="NFO", action="BUY", quantity=1)],
    )
    with patch("nautilus_openalgo_bridge.preflight.is_bridge_market_open", return_value=True):
        result = run_preflight(intent, client)
    assert result["blocked"] is True
    assert result["reason"] == "margin_unavailable"


def test_preflight_enter_blocked_when_margin_exceeds_budget():
    client = MagicMock()
    client.ensure_analyzer_mode.return_value = True
    client.calculate_margin.return_value = 50000.0
    intent = ExecutionIntent(
        action=IntentAction.ENTER,
        agent_id="aa_budget",
        rationale="go",
        legs=[ExecutionLeg(symbol="X", exchange="NFO", action="BUY", quantity=1)],
    )
    agent = {"id": "aa_budget", "constraints": {"budget_inr": 20000}}
    with patch("nautilus_openalgo_bridge.preflight.is_bridge_market_open", return_value=True), patch(
        "trade_integrations.autonomous_agents.store.get_agent", return_value=agent
    ):
        result = run_preflight(intent, client)
    assert result["blocked"] is True
    assert result["reason"] == "margin_exceeds_budget"


def test_preflight_enter_blocked_when_budget_check_fails():
    client = MagicMock()
    client.ensure_analyzer_mode.return_value = True
    client.calculate_margin.return_value = 5000.0
    intent = ExecutionIntent(
        action=IntentAction.ENTER,
        agent_id="aa_budget",
        rationale="go",
        legs=[ExecutionLeg(symbol="X", exchange="NFO", action="BUY", quantity=1)],
    )
    with patch("nautilus_openalgo_bridge.preflight.is_bridge_market_open", return_value=True), patch(
        "trade_integrations.autonomous_agents.store.get_agent",
        side_effect=RuntimeError("store down"),
    ):
        result = run_preflight(intent, client)
    assert result["blocked"] is True
    assert result["reason"] == "budget_check_failed"


def test_preflight_enter_blocked_outside_hours():
    client = MagicMock()
    client.ensure_analyzer_mode.return_value = True
    intent = ExecutionIntent(
        action=IntentAction.ENTER,
        agent_id="aa_x",
        rationale="go",
        legs=[ExecutionLeg(symbol="X", exchange="NFO", action="BUY", quantity=1)],
    )
    with patch("nautilus_openalgo_bridge.preflight.is_bridge_market_open", return_value=False):
        result = run_preflight(intent, client)
    assert result["blocked"] is True
    assert result["reason"] == "outside_market_hours"
