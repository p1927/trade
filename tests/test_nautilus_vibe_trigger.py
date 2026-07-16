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
    # streaming must stay True until session service finalizes the turn
    last_saved = mock_save.call_args_list[-1][0][0]
    assert last_saved.get("streaming") is True


@patch("nautilus_openalgo_bridge.vibe_trigger.get_agent")
@patch("nautilus_openalgo_bridge.vibe_trigger.save_agent")
@patch("nautilus_openalgo_bridge.vibe_trigger.make_vibe_message_client")
def test_dispatch_watch_alert_sync_error_clears_streaming(mock_client_factory, mock_save, mock_get_agent):
    agent = {
        "id": "aa_test",
        "status": "running",
        "vibe_session_id": "sess123",
        "streaming": False,
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
