"""Tests for autonomous agent MCP actions."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
INTEGRATIONS = ROOT / "integrations"
if str(INTEGRATIONS) not in sys.path:
    sys.path.insert(0, str(INTEGRATIONS))

from trade_integrations.autonomous_agents.mcp_actions import mcp_record_decision  # noqa: E402
from trade_integrations.autonomous_agents.store import get_agent, save_agent  # noqa: E402


@pytest.fixture
def hub_tmp(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    hub = tmp_path / "hub"
    hub.mkdir()
    monkeypatch.setenv("TRADE_STACK_HUB_DIR", str(hub))
    return hub


def _us_agent(agent_id: str = "aa_test") -> dict:
    return {
        "id": agent_id,
        "type": "autonomous_agent.instance",
        "name": "Test US",
        "status": "running",
        "symbols": ["SPY"],
        "execution_market": "US",
        "constraints": {"mode": "paper"},
        "mandate": "Paper trade SPY.",
        "vibe_session_id": "sess_1",
    }


def _in_agent(agent_id: str = "aa_in") -> dict:
    return {
        "id": agent_id,
        "type": "autonomous_agent.instance",
        "name": "Test IN",
        "status": "running",
        "symbols": ["NIFTY"],
        "execution_market": "IN",
        "constraints": {"mode": "paper"},
        "mandate": "Paper trade NIFTY options.",
        "mandate_config": {"allowed_instruments": ["options"]},
        "vibe_session_id": "sess_2",
    }


def test_record_decision_persists_thesis_us_agent(hub_tmp: Path):
    save_agent(_us_agent())
    result = mcp_record_decision(
        agent_id="aa_test",
        decision="HOLD",
        rationale="Low IV, range-bound",
        confidence=40,
        direction="neutral",
        strategy="iron_condor",
    )
    assert result["status"] == "ok"
    agent = get_agent("aa_test")
    assert agent is not None
    assert agent["thesis"]["confidence"] == 40
    assert agent["thesis"]["strategy"] == "iron_condor"
    assert agent["thesis"]["direction"] == "neutral"
    assert agent["thesis"]["decision"] == "HOLD"
    assert agent["last_decision"]["decision"] == "HOLD"
    assert agent["last_decision"]["confidence"] == 40


def test_record_decision_persists_thesis_in_agent(hub_tmp: Path):
    save_agent(_in_agent())

    result = mcp_record_decision(
        agent_id="aa_in",
        decision="HOLD",
        rationale="Below confidence gate",
        confidence=55,
        direction="neutral",
        strategy="long_straddle",
    )
    assert result["status"] == "ok"
    agent = get_agent("aa_in")
    assert agent["thesis"]["confidence"] == 55
    assert agent["thesis"]["strategy"] == "long_straddle"
    assert agent["last_decision"]["confidence"] == 55
    assert agent.get("lifecycle") is not None


def test_record_decision_preserves_existing_thesis_fields(hub_tmp: Path):
    agent = _us_agent("aa_keep")
    agent["thesis"] = {"entry_spot": 24000.0, "underlying": "NIFTY"}
    save_agent(agent)
    mcp_record_decision(
        agent_id="aa_keep",
        decision="HOLD",
        rationale="Wait",
        confidence=30,
    )
    agent = get_agent("aa_keep")
    assert agent["thesis"]["entry_spot"] == 24000.0
    assert agent["thesis"]["confidence"] == 30
