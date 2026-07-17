"""Tests for plan approval gate."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
INTEGRATIONS = ROOT / "integrations"
if str(INTEGRATIONS) not in sys.path:
    sys.path.insert(0, str(INTEGRATIONS))

from trade_integrations.autonomous_agents.plan_approval import (  # noqa: E402
    approve_agent_plan,
    is_awaiting_plan_approval,
    is_plan_approved,
)
from trade_integrations.autonomous_agents.store import get_agent, save_agent  # noqa: E402


@pytest.fixture
def hub_tmp(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    hub = tmp_path / "hub"
    hub.mkdir()
    monkeypatch.setenv("TRADE_STACK_HUB_DIR", str(hub))
    return hub


def _agent(agent_id: str = "aa_plan") -> dict:
    return {
        "id": agent_id,
        "type": "autonomous_agent.instance",
        "name": "Plan Test",
        "status": "running",
        "symbols": ["RELIANCE"],
        "bootstrap_status": "awaiting_plan_approval",
        "plan_approval_required": True,
        "watch_spec": {"rules": [{"symbol": "RELIANCE", "metric": "spot_move_pct", "threshold": 1.0}], "strategy": "hold_cash"},
        "vibe_session_id": "sess_plan",
    }


def test_is_awaiting_plan_approval(hub_tmp: Path):
    save_agent(_agent())
    agent = get_agent("aa_plan")
    assert is_awaiting_plan_approval(agent) is True
    assert is_plan_approved(agent) is False


def test_approve_agent_plan(hub_tmp: Path):
    save_agent(_agent())
    result = approve_agent_plan("aa_plan")
    assert result["status"] == "ok"
    updated = get_agent("aa_plan")
    assert updated["bootstrap_status"] == "done"
    assert updated.get("plan_approved_at")
    assert is_plan_approved(updated) is True
