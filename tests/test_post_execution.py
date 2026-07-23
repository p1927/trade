"""Tests for post-execution turn scheduling."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

ROOT = Path(__file__).resolve().parents[1]
INTEGRATIONS = ROOT / "integrations"
if str(INTEGRATIONS) not in sys.path:
    sys.path.insert(0, str(INTEGRATIONS))

from nautilus_openalgo_bridge.models import IntentAction  # noqa: E402
from trade_integrations.autonomous_agents.post_execution import (  # noqa: E402
    material_state_change,
    should_dispatch_post_execution,
)


def test_material_state_change_on_position_delta():
    assert material_state_change({"open_positions": 0}, {"open_positions": 2}) is True
    assert material_state_change({"open_positions": 1}, {"open_positions": 1}) is False


def test_should_dispatch_when_positions_change():
    agent = {
        "id": "aa_pe",
        "status": "running",
        "streaming": False,
        "plan_approved_at": "2026-07-01T00:00:00+00:00",
    }
    ok, reason = should_dispatch_post_execution(
        agent,
        pre={"open_positions": 0, "legs": 0},
        post={"open_positions": 1, "legs": 1},
        execution_status="executed",
    )
    assert ok is True
    assert reason == "ok"


def test_should_skip_when_streaming():
    agent = {
        "id": "aa_pe",
        "status": "running",
        "streaming": True,
        "plan_approved_at": "2026-07-01T00:00:00+00:00",
    }
    ok, reason = should_dispatch_post_execution(
        agent,
        pre={"open_positions": 0},
        post={"open_positions": 1},
        execution_status="executed",
    )
    assert ok is False
    assert reason == "turn_in_flight"


@pytest.mark.asyncio
async def test_reconcile_schedules_post_execution(hub_tmp: Path):
    from nautilus_openalgo_bridge.models import ExecutionIntent
    from nautilus_openalgo_bridge.reconcile import reconcile_after_intent
    from trade_integrations.autonomous_agents.store import save_agent

    save_agent(
        {
            "id": "aa_pe2",
            "status": "running",
            "plan_approved_at": "2026-07-01T00:00:00+00:00",
            "symbols": ["NIFTY"],
            "vibe_session_id": "sess_pe",
        }
    )
    intent = ExecutionIntent(
        action=IntentAction.ENTER,
        agent_id="aa_pe2",
        rationale="test",
        underlying="NIFTY",
    )
    with patch("nautilus_openalgo_bridge.reconcile.get_openalgo_client") as mock_client_factory, patch(
        "trade_integrations.autonomous_agents.post_execution.schedule_post_execution_turn"
    ) as mock_schedule:
        client = mock_client_factory.return_value
        client.get_position_book.return_value = [
            {"symbol": "NIFTY24JUL24500CE", "quantity": 50, "pnl": 10.0, "strategy": "aa_pe2"}
        ]
        client.get_orderbook.return_value = [
            {"symbol": "NIFTY24JUL24500CE", "status": "complete", "strategy": "aa_pe2", "action": "BUY", "quantity": 50}
        ]
        client.get_symbol_info.return_value = {"symbol": "NIFTY24JUL24500CE"}
        with patch(
            "nautilus_openalgo_bridge.reconcile.mcp_record_decision",
            create=True,
        ), patch(
            "trade_integrations.autonomous_agents.mcp_actions.mcp_record_decision",
            return_value={"status": "ok"},
        ):
            payload = reconcile_after_intent(
                intent,
                client=client,
                execution_result={"status": "executed"},
                pre_snapshot={"open_positions": 0, "legs": 0},
            )
        assert payload["open_positions"] == 1
        mock_schedule.assert_called_once()


@pytest.fixture
def hub_tmp(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    hub = tmp_path / "hub"
    hub.mkdir()
    monkeypatch.setenv("TRADE_STACK_HUB_DIR", str(hub))
    return hub
