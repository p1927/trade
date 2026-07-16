"""Integration tests for orchestrator propose → commit flow."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

AGENT_SRC = Path(__file__).resolve().parents[1] / "vibetrading" / "agent"
if str(AGENT_SRC) not in sys.path:
    sys.path.insert(0, str(AGENT_SRC))


@pytest.fixture
def agents_hub(tmp_path, monkeypatch):
    hub = tmp_path / "hub"
    (hub / "_data" / "autonomous_agents" / "proposals").mkdir(parents=True)
    monkeypatch.setattr(
        "trade_integrations.context.hub.get_hub_dir",
        lambda: hub,
    )
    return hub


@pytest.mark.unit
class TestOrchestratorProposeFlow:
    def test_propose_saves_with_orchestrator_session(self, agents_hub, monkeypatch):
        from trade_integrations.autonomous_agents import proposals
        from trade_integrations.autonomous_agents.store import load_proposal

        monkeypatch.setattr(
            proposals,
            "build_stack_health",
            lambda: {"vibe_scheduler": "ok"},
        )
        result = proposals.propose_autonomous_agent(
            symbols=["NIFTY"],
            name="NIFTY bot",
            mandate="Paper event vol",
            orchestrator_session_id="orch_flow1",
        )
        assert result["status"] == "ready"
        pid = result["proposal_id"]
        saved = load_proposal(pid)
        assert saved is not None
        assert saved.get("orchestrator_session_id") == "orch_flow1"

    def test_repropose_supersedes_prior(self, agents_hub, monkeypatch):
        from trade_integrations.autonomous_agents import proposals
        from trade_integrations.autonomous_agents.store import load_proposal

        monkeypatch.setattr(
            proposals,
            "build_stack_health",
            lambda: {"vibe_scheduler": "ok"},
        )
        first = proposals.propose_autonomous_agent(
            symbols=["NIFTY"],
            name="First",
            mandate="Paper",
            orchestrator_session_id="orch_flow2",
        )
        second = proposals.propose_autonomous_agent(
            symbols=["BANKNIFTY"],
            name="Second",
            mandate="Paper",
            orchestrator_session_id="orch_flow2",
        )
        assert first["proposal_id"] != second["proposal_id"]
        old = load_proposal(first["proposal_id"])
        assert old is not None
        assert old.get("superseded") is True
