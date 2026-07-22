"""Tests for Vibe alert dispatch (mocked HTTP)."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

ROOT = Path(__file__).resolve().parents[1]
INTEGRATIONS = ROOT / "integrations"
if str(INTEGRATIONS) not in sys.path:
    sys.path.insert(0, str(INTEGRATIONS))

from nautilus_openalgo_bridge.models import (  # noqa: E402
    BridgeSignal,
    QuoteSnapshot,
    WatchAlert,
    WatchRule,
)
from nautilus_openalgo_bridge.vibe_trigger import (  # noqa: E402
    build_alert_turn_prompt,
    build_bridge_alert_block,
    dispatch_watch_alert_sync,
)


def test_build_bridge_alert_block_includes_message():
    alert = WatchAlert(
        signal=BridgeSignal.REVIEW_NEEDED,
        rule=WatchRule(symbol="NIFTY", metric="spot_move_pct", threshold=0.5),
        symbol="NIFTY",
        message="NIFTY moved +0.62%",
        ltp=24600.0,
        move_pct=0.62,
    )
    block = build_bridge_alert_block(
        alert,
        quotes={"NIFTY": QuoteSnapshot(symbol="NIFTY", exchange="NSE", ltp=24600.0)},
    )
    assert "Nautilus watch alert" in block
    assert "NIFTY moved +0.62%" in block
    assert "24600" in block


def test_build_alert_turn_prompt_includes_mandate():
    agent = {
        "id": "aa_test",
        "name": "Test agent",
        "symbols": ["NIFTY"],
        "mandate": "Watch event vol",
        "constraints": {"confidence_threshold": 75, "mode": "paper"},
    }
    alert = WatchAlert(
        signal=BridgeSignal.REVIEW_NEEDED,
        rule=None,
        symbol="NIFTY",
        message="test",
    )
    prompt = build_alert_turn_prompt(agent=agent, alert=alert)
    assert "Nautilus watch alert" in prompt
    assert "strategy_revision" in prompt
    assert "Watch event vol" in prompt


@patch("nautilus_openalgo_bridge.vibe_trigger.get_agent")
@patch("nautilus_openalgo_bridge.vibe_trigger.save_agent")
@patch("nautilus_openalgo_bridge.vibe_trigger.make_vibe_message_client")
def test_dispatch_watch_alert_sync_success(mock_client_factory, mock_save, mock_get_agent):
    agent = {
        "id": "aa_test",
        "status": "running",
        "vibe_session_id": "sess123",
        "streaming": False,
        "plan_approved_at": "2026-07-01T00:00:00+00:00",
    }
    mock_get_agent.side_effect = lambda _id: dict(agent)

    async def _caller(session_id: str, content: str) -> dict:
        assert session_id == "sess123"
        assert "Nautilus watch alert" in content
        return {"status": "accepted"}

    mock_client_factory.return_value = _caller

    alert = WatchAlert(
        signal=BridgeSignal.REVIEW_NEEDED,
        rule=None,
        symbol="NIFTY",
        message="move",
    )
    result = dispatch_watch_alert_sync("aa_test", alert)
    assert result["status"] == "dispatched"
    assert mock_save.called
    last_saved = mock_save.call_args_list[-1][0][0]
    assert last_saved.get("streaming") is True
    assert last_saved.get("last_vibe_dispatch_at")


@patch("nautilus_openalgo_bridge.vibe_trigger.get_agent")
@patch("nautilus_openalgo_bridge.vibe_trigger.save_agent")
@patch("nautilus_openalgo_bridge.vibe_trigger.make_vibe_message_client")
def test_dispatch_watch_alert_sync_error_clears_streaming(mock_client_factory, mock_save, mock_get_agent):
    agent = {
        "id": "aa_test",
        "status": "running",
        "vibe_session_id": "sess123",
        "streaming": False,
        "plan_approved_at": "2026-07-01T00:00:00+00:00",
    }
    mock_get_agent.side_effect = lambda _id: dict(agent)

    async def _caller(session_id: str, content: str) -> dict:
        raise RuntimeError("Vibe API 500")

    mock_client_factory.return_value = _caller

    alert = WatchAlert(
        signal=BridgeSignal.REVIEW_NEEDED,
        rule=None,
        symbol="NIFTY",
        message="move",
    )
    result = dispatch_watch_alert_sync("aa_test", alert)
    assert result["status"] == "error"
    last_saved = mock_save.call_args_list[-1][0][0]
    assert last_saved.get("streaming") is False
    assert not last_saved.get("last_vibe_dispatch_at")


@patch("nautilus_openalgo_bridge.vibe_trigger.get_agent")
def test_dispatch_skips_when_plan_not_approved(mock_get_agent):
    mock_get_agent.return_value = {
        "id": "aa_test",
        "status": "running",
        "vibe_session_id": "s1",
        "bootstrap_status": "awaiting_plan_approval",
    }
    alert = WatchAlert(
        signal=BridgeSignal.REVIEW_NEEDED,
        rule=None,
        symbol="NIFTY",
        message="move",
    )
    result = dispatch_watch_alert_sync("aa_test", alert)
    assert result["status"] == "skipped"
    assert result.get("reason") == "plan_not_approved"


@patch("nautilus_openalgo_bridge.vibe_trigger.get_agent")
def test_dispatch_skips_when_not_running(mock_get_agent):
    mock_get_agent.return_value = {"id": "aa_test", "status": "paused", "vibe_session_id": "s1"}
    alert = WatchAlert(
        signal=BridgeSignal.REVIEW_NEEDED,
        rule=None,
        symbol="NIFTY",
        message="move",
    )
    result = dispatch_watch_alert_sync("aa_test", alert)
    assert result["status"] == "skipped"


@patch("nautilus_openalgo_bridge.vibe_trigger.get_agent")
@patch("nautilus_openalgo_bridge.vibe_trigger.save_agent")
@patch("nautilus_openalgo_bridge.vibe_trigger.make_vibe_message_client")
def test_dispatch_skips_within_skip_if_unchanged_gate(mock_client_factory, mock_save, mock_get_agent):
    from datetime import datetime, timezone

    agent = {
        "id": "aa_test",
        "status": "running",
        "vibe_session_id": "sess123",
        "streaming": False,
        "plan_approved_at": "2026-07-01T00:00:00+00:00",
        "watch_spec": {"gate": {"skip_if_unchanged_minutes": 30}},
        "last_vibe_dispatch_at": datetime.now(timezone.utc).isoformat(),
    }
    mock_get_agent.return_value = agent
    alert = WatchAlert(
        signal=BridgeSignal.REVIEW_NEEDED,
        rule=None,
        symbol="NIFTY",
        message="move",
    )
    result = dispatch_watch_alert_sync("aa_test", alert)
    assert result["status"] == "skipped"
    assert result.get("reason") == "skip_if_unchanged_gate"
    mock_client_factory.assert_not_called()
    mock_save.assert_not_called()
