"""Tests for execution pre-flight checks."""

from __future__ import annotations

import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

ROOT = Path(__file__).resolve().parents[1]
INTEGRATIONS = ROOT / "integrations"
if str(INTEGRATIONS) not in sys.path:
    sys.path.insert(0, str(INTEGRATIONS))

from nautilus_openalgo_bridge.models import ExecutionIntent, ExecutionLeg, IntentAction, PositionHandoff  # noqa: E402
from nautilus_openalgo_bridge.preflight import (  # noqa: E402
    STALE_HANDOFF_CONTEXT_MINUTES,
    _handoff_market_context_stale,
    run_preflight,
)
from trade_integrations.openalgo.market_context import MarketContext  # noqa: E402


def _paper_context(*, analyze: bool = True) -> MarketContext:
    return MarketContext(
        context_generation="2026-07-23T09:15:00+05:30",
        data_broker="zerodha",
        execution_venue="sandbox" if analyze else "broker",
        analyze_mode=analyze,
        market_region="IN",
        positions_authority="sandbox.db" if analyze else "broker",
        quotes_source="broker_plugin",
        simulator={"active": False},
        capabilities=("equity",),
    )


def _client_with_context(*, analyze: bool = True) -> MagicMock:
    client = MagicMock()
    client.get_market_context.return_value = _paper_context(analyze=analyze)
    client.ensure_analyzer_mode.return_value = True
    return client


def test_preflight_exit_blocked_for_autonomous_outside_window():
    client = _client_with_context()
    intent = ExecutionIntent(action=IntentAction.EXIT, agent_id="aa_x", rationale="flatten")
    with patch("nautilus_openalgo_bridge.market_hours.is_exit_window_open_for_agent", return_value=False):
        result = run_preflight(intent, client)
    assert result["blocked"] is True
    assert result["reason"] == "outside_exit_window"


def test_preflight_exit_analyzer_bypass_only_without_agent_id(monkeypatch):
    monkeypatch.setenv("ANALYZER", "1")
    client = _client_with_context()
    intent = ExecutionIntent(action=IntentAction.EXIT, agent_id="", rationale="flatten")
    with patch("nautilus_openalgo_bridge.market_hours.is_exit_window_open_for_agent", return_value=False):
        result = run_preflight(intent, client)
    assert result["blocked"] is False
    assert result["checks"].get("paper_exit_analyzer_bypass") is True


def test_preflight_exit_blocked_outside_window_when_not_paper():
    client = _client_with_context()
    intent = ExecutionIntent(action=IntentAction.EXIT, agent_id="aa_x", rationale="flatten")
    with patch("nautilus_openalgo_bridge.preflight.get_bridge_config") as mock_cfg, patch(
        "nautilus_openalgo_bridge.market_hours.is_exit_window_open_for_agent", return_value=False
    ):
        mock_cfg.return_value.paper_only = False
        result = run_preflight(intent, client)
    assert result["blocked"] is True
    assert result["reason"] == "outside_exit_window"


def test_preflight_exit_allowed_for_us_agent_when_us_window_open():
    client = _client_with_context()
    intent = ExecutionIntent(action=IntentAction.EXIT, agent_id="aa_spy", rationale="flatten")
    with patch("nautilus_openalgo_bridge.market_hours.agent_market", return_value="US"), patch(
        "nautilus_openalgo_bridge.market_hours.is_us_exit_window_open", return_value=True
    ), patch("nautilus_openalgo_bridge.config.is_bridge_exit_window_open", return_value=False):
        result = run_preflight(intent, client)
    assert result["blocked"] is False
    assert result["checks"].get("exit_window_open") is True


def test_preflight_blocked_when_paper_mandate_live_openalgo():
    client = _client_with_context(analyze=False)
    client.ensure_analyzer_mode.return_value = False
    intent = ExecutionIntent(
        action=IntentAction.ENTER,
        agent_id="aa_paper",
        rationale="go",
        legs=[ExecutionLeg(symbol="X", exchange="NFO", action="BUY", quantity=1)],
    )
    agent = {"id": "aa_paper", "constraints": {"mode": "paper", "budget_inr": 50_000}}
    with patch("nautilus_openalgo_bridge.preflight.is_bridge_market_open", return_value=True), patch(
        "trade_integrations.autonomous_agents.store.get_agent",
        return_value=agent,
    ):
        result = run_preflight(intent, client)
    assert result["blocked"] is True
    assert result["reason"] == "analyzer_mode"


def test_preflight_blocked_when_market_context_fetch_fails_for_autonomous():
    client = _client_with_context()
    client.get_market_context.side_effect = RuntimeError("openalgo down")
    intent = ExecutionIntent(
        action=IntentAction.ENTER,
        agent_id="aa_paper",
        rationale="go",
        legs=[ExecutionLeg(symbol="X", exchange="NFO", action="BUY", quantity=1)],
    )
    with patch("nautilus_openalgo_bridge.preflight.get_bridge_config") as mock_cfg, patch(
        "nautilus_openalgo_bridge.preflight.is_bridge_market_open", return_value=True
    ):
        mock_cfg.return_value.paper_only = False
        result = run_preflight(intent, client)
    assert result["blocked"] is True
    assert result["reason"] == "market_context_unavailable"


def test_preflight_enter_checks_margin():
    client = _client_with_context()
    client.calculate_margin.return_value = 12500.0
    intent = ExecutionIntent(
        action=IntentAction.ENTER,
        agent_id="aa_x",
        rationale="go",
        legs=[
            ExecutionLeg(symbol="NIFTY24JUL24500CE", exchange="NFO", action="SELL", quantity=50),
        ],
    )
    with patch("nautilus_openalgo_bridge.preflight.is_bridge_market_open", return_value=True), patch(
        "trade_integrations.autonomous_agents.store.get_agent",
        return_value={"id": "aa_x", "constraints": {"budget_inr": 50_000}},
    ):
        result = run_preflight(intent, client)
    assert result["blocked"] is False
    assert result["checks"]["margin_inr"] == 12500.0
    assert result["checks"]["context_generation"]


def test_preflight_enter_blocked_when_margin_unavailable():
    client = _client_with_context()
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
    client = _client_with_context()
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


def test_preflight_enter_blocked_when_agent_not_found():
    client = _client_with_context()
    client.calculate_margin.return_value = 5000.0
    intent = ExecutionIntent(
        action=IntentAction.ENTER,
        agent_id="aa_missing",
        rationale="go",
        legs=[ExecutionLeg(symbol="X", exchange="NFO", action="BUY", quantity=1)],
    )
    with patch("nautilus_openalgo_bridge.preflight.is_bridge_market_open", return_value=True), patch(
        "trade_integrations.autonomous_agents.store.get_agent", return_value=None
    ):
        result = run_preflight(intent, client)
    assert result["blocked"] is True
    assert result["reason"] == "agent_not_found"


def test_preflight_enter_blocked_when_budget_check_fails():
    client = _client_with_context()
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


def test_preflight_enter_blocked_when_charges_exceed_max_daily_loss():
    client = _client_with_context()
    client.calculate_margin.return_value = 5000.0
    intent = ExecutionIntent(
        action=IntentAction.ENTER,
        agent_id="aa_charges",
        rationale="go",
        legs=[ExecutionLeg(symbol="X", exchange="NFO", action="BUY", quantity=1, price=100)],
    )
    agent = {"id": "aa_charges", "constraints": {"budget_inr": 50000, "max_daily_loss_inr": 1}}
    with patch("nautilus_openalgo_bridge.preflight.is_bridge_market_open", return_value=True), patch(
        "trade_integrations.autonomous_agents.store.get_agent", return_value=agent
    ), patch(
        "nautilus_openalgo_bridge.preflight._estimate_charges_for_orders",
        return_value={"round_trip_charges": 50.0, "total": {"total_charges": 50.0}},
    ):
        result = run_preflight(intent, client)
    assert result["blocked"] is True
    assert result["reason"] == "charges_exceed_max_daily_loss"


def test_preflight_enter_blocked_outside_hours():
    client = _client_with_context()
    intent = ExecutionIntent(
        action=IntentAction.ENTER,
        agent_id="aa_x",
        rationale="go",
        legs=[ExecutionLeg(symbol="X", exchange="NFO", action="BUY", quantity=1)],
    )
    with patch("nautilus_openalgo_bridge.market_hours.is_agent_watch_session_open", return_value=False):
        result = run_preflight(intent, client)
    assert result["blocked"] is True
    assert result["reason"] == "outside_market_hours"


def test_preflight_enter_allowed_for_us_agent_when_us_session_open():
    client = _client_with_context()
    intent = ExecutionIntent(
        action=IntentAction.ENTER,
        agent_id="aa_spy",
        rationale="go",
        legs=[ExecutionLeg(symbol="SPY", exchange="NASDAQ", action="BUY", quantity=1)],
    )
    with patch("nautilus_openalgo_bridge.market_hours.is_agent_watch_session_open", return_value=True), patch(
        "nautilus_openalgo_bridge.preflight.is_bridge_market_open", return_value=False
    ), patch(
        "nautilus_openalgo_bridge.preflight.legs_to_openalgo_orders",
        return_value=[{"symbol": "SPY"}],
    ), patch.object(client, "calculate_margin", return_value=100.0), patch(
        "trade_integrations.autonomous_agents.store.get_agent",
        return_value={"constraints": {"budget_inr": 50000, "max_daily_loss_inr": 5000}},
    ), patch(
        "nautilus_openalgo_bridge.preflight._estimate_charges_for_orders",
        return_value={"total": {"total_charges": 1.0}},
    ):
        result = run_preflight(intent, client)
    assert result["blocked"] is False
    assert result["checks"].get("market_open") is True


def test_handoff_market_context_stale_by_age():
    old = (datetime.now(timezone.utc) - timedelta(minutes=STALE_HANDOFF_CONTEXT_MINUTES + 1)).isoformat()
    current = datetime.now(timezone.utc).isoformat()
    assert _handoff_market_context_stale(old, current) is True


def test_handoff_market_context_fresh_match():
    fresh = datetime.now(timezone.utc).isoformat()
    assert _handoff_market_context_stale(fresh, fresh) is False


def test_handoff_market_context_stale_synthetic_by_mtime():
    synthetic = "alpaca-alpaca-paper-sdk-synthetic"
    old_mtime = time.time() - (STALE_HANDOFF_CONTEXT_MINUTES + 5) * 60
    assert _handoff_market_context_stale(
        synthetic,
        synthetic,
        handoff_file_mtime=old_mtime,
    ) is True


def test_preflight_blocked_stale_handoff_context(hub_tmp):
    old_gen = (datetime.now(timezone.utc) - timedelta(minutes=20)).isoformat()
    handoff = PositionHandoff(
        agent_id="aa_stale",
        widget_id=None,
        underlying="NIFTY",
        legs=[],
        entry_spot=0.0,
        context_generation=old_gen,
    )
    client = _client_with_context()
    current_gen = datetime.now(timezone.utc).isoformat()
    client.get_market_context.return_value = MarketContext(
        context_generation=current_gen,
        data_broker="zerodha",
        execution_venue="sandbox",
        analyze_mode=True,
        market_region="IN",
        positions_authority="sandbox.db",
        quotes_source="broker_plugin",
        simulator={"active": False},
        capabilities=("equity",),
    )
    intent = ExecutionIntent(
        action=IntentAction.EXIT,
        agent_id="aa_stale",
        rationale="flatten",
    )
    with patch("nautilus_openalgo_bridge.handoff.load_handoff", return_value=handoff):
        result = run_preflight(intent, client)
    assert result["blocked"] is True
    assert result["reason"] == "stale_market_context"
    assert "reload_hint" in result
    assert result["checks"]["handoff_context_generation"] == old_gen


@pytest.fixture
def hub_tmp(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    hub = tmp_path / "hub"
    hub.mkdir()
    monkeypatch.setenv("TRADE_STACK_HUB_DIR", str(hub))
    return hub
