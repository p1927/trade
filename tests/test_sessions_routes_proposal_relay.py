"""Tests for autonomous proposal SSE relay from tool_result events."""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

_AGENT_SRC = Path(__file__).resolve().parents[1] / "vibetrading" / "agent" / "src"
if str(_AGENT_SRC) not in sys.path:
    sys.path.insert(0, str(_AGENT_SRC.parent))


@pytest.fixture
def relay_module():
    from src.api import sessions_routes

    return sessions_routes


def test_autonomous_propose_relay_local_tool_name(relay_module) -> None:
    sample = {
        "type": "autonomous_agent.proposal",
        "proposal_id": "aap_00000000000000000000000000000001",
        "status": "ready",
        "symbols": ["NIFTY"],
    }
    relay_module._load_autonomous_proposal = lambda _pid: sample  # type: ignore[method-assign]

    event = SimpleNamespace(
        event_type="tool_result",
        session_id="sess1",
        data={
            "tool": "propose_autonomous_agent",
            "status": "ok",
            "preview": '{"proposal_id": "aap_00000000000000000000000000000001", "status": "ready"}',
        },
    )
    frame = relay_module._autonomous_agent_proposal_frame_from_tool_result(event)
    assert frame is not None
    assert "autonomous_agent.proposal" in frame
    assert "aap_00000000000000000000000000000001" in frame


def test_autonomous_propose_relay_mcp_tool_name(relay_module) -> None:
    sample = {
        "type": "autonomous_agent.proposal",
        "proposal_id": "aap_00000000000000000000000000000002",
        "status": "ready",
        "symbols": ["RELIANCE"],
    }
    relay_module._load_autonomous_proposal = lambda _pid: sample  # type: ignore[method-assign]

    event = SimpleNamespace(
        event_type="tool_result",
        session_id="sess1",
        data={
            "tool": "mcp_openalgo_propose_autonomous_agent",
            "status": "ok",
            "preview": '{"proposal_id": "aap_00000000000000000000000000000002", "status": "ready"}',
        },
    )
    frame = relay_module._autonomous_agent_proposal_frame_from_tool_result(event)
    assert frame is not None
    assert "aap_00000000000000000000000000000002" in frame


def test_autonomous_propose_relay_incomplete_status(relay_module) -> None:
    sample = {
        "type": "autonomous_agent.proposal",
        "proposal_id": "aap_00000000000000000000000000000003",
        "status": "incomplete",
        "missing_fields": ["allowed_instruments"],
        "symbols": ["RELIANCE"],
    }
    relay_module._load_autonomous_proposal = lambda _pid: sample  # type: ignore[method-assign]

    event = SimpleNamespace(
        event_type="tool_result",
        session_id="sess1",
        data={
            "tool": "propose_autonomous_agent",
            "status": "ok",
            "preview": '{"status": "incomplete", "proposal_id": "aap_00000000000000000000000000000003"}',
        },
    )
    frame = relay_module._autonomous_agent_proposal_frame_from_tool_result(event)
    assert frame is not None
    assert "incomplete" in frame


def test_autonomous_propose_relay_ignores_unrelated_tool(relay_module) -> None:
    event = SimpleNamespace(
        event_type="tool_result",
        session_id="sess1",
        data={"tool": "get_stock_browse", "status": "ok", "preview": "{}"},
    )
    assert relay_module._autonomous_agent_proposal_frame_from_tool_result(event) is None
