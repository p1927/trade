"""Tests for fail-closed execution guards on bridge/autonomous agents."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
INTEGRATIONS = ROOT / "integrations"
if str(INTEGRATIONS) not in sys.path:
    sys.path.insert(0, str(INTEGRATIONS))

from trade_integrations.execution.enforce import (  # noqa: E402
    assert_direct_order_tool_allowed,
    is_bridge_autonomous_agent,
)


@pytest.fixture
def hub_tmp(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    hub = tmp_path / "hub"
    hub.mkdir()
    monkeypatch.setenv("TRADE_STACK_HUB_DIR", str(hub))
    return hub


def test_direct_basket_blocked_for_autonomous_session_kind():
    with pytest.raises(PermissionError, match="blocked"):
        assert_direct_order_tool_allowed(
            tool_name="place_basket_order",
            session_kind="autonomous_agent",
        )


def test_direct_basket_allowed_for_interactive_session():
    assert_direct_order_tool_allowed(
        tool_name="place_basket_order",
        session_kind="interactive",
    )


def test_bridge_agent_detected(hub_tmp: Path):
    from trade_integrations.autonomous_agents.store import save_agent

    save_agent(
        {
            "id": "aa_exec",
            "type": "autonomous_agent.instance",
            "symbols": ["NIFTY"],
            "execution_market": "IN",
            "constraints": {"mode": "paper"},
            "mandate": "Paper trade NIFTY options",
            "status": "running",
        }
    )
    assert is_bridge_autonomous_agent("aa_exec") is True


def test_mcp_slug_blocked_for_bridge_agent(hub_tmp: Path):
    from trade_integrations.autonomous_agents.store import save_agent

    save_agent(
        {
            "id": "aa_mcp",
            "type": "autonomous_agent.instance",
            "symbols": ["NIFTY"],
            "execution_market": "IN",
            "constraints": {"mode": "paper"},
            "mandate": "Paper trade NIFTY options",
            "status": "running",
        }
    )
    with pytest.raises(PermissionError):
        assert_direct_order_tool_allowed(
            tool_name="mcp_openalgo_place_basket_order",
            autonomous_agent_id="aa_mcp",
        )
