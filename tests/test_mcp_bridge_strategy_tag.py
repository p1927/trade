"""MCP bridge intent strategy tag tests."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

ROOT = Path(__file__).resolve().parents[1]
INTEGRATIONS = ROOT / "integrations"
if str(INTEGRATIONS) not in sys.path:
    sys.path.insert(0, str(INTEGRATIONS))


@pytest.mark.unit
def test_mcp_submit_bridge_execution_intent_uses_agent_strategy_tag(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict = {}

    def fake_submit(intent):
        captured["strategy"] = intent.strategy
        return Path("/tmp/intent.json")

    monkeypatch.setattr(
        "trade_integrations.autonomous_agents.mcp_actions.get_agent",
        lambda agent_id: {
            "id": agent_id,
            "symbols": ["NIFTY"],
            "constraints": {"mode": "paper"},
            "mandate_config": {"allowed_instruments": ["options"]},
        },
    )
    monkeypatch.setattr(
        "trade_integrations.autonomous_agents.mcp_actions.resolve_profile",
        lambda agent: MagicMock(uses_nautilus_handoff=True),
    )
    monkeypatch.setattr(
        "nautilus_openalgo_bridge.intent_queue.submit_intent",
        fake_submit,
    )
    monkeypatch.setattr(
        "nautilus_openalgo_bridge.intent_queue.process_pending_intents",
        lambda max_count=1: [],
    )

    from trade_integrations.autonomous_agents.mcp_actions import mcp_submit_bridge_execution_intent

    result = mcp_submit_bridge_execution_intent(
        agent_id="aa_mcp_tag",
        action="ENTER",
        rationale="test",
    )
    assert result["status"] == "submitted"
    assert captured["strategy"] == "aa_mcp_tag"
