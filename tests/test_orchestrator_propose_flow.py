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

    def test_commit_latest_after_repropose(self, agents_hub, monkeypatch):
        from trade_integrations.autonomous_agents import proposals
        from trade_integrations.autonomous_agents.store import load_proposal

        monkeypatch.setattr(
            proposals,
            "build_stack_health",
            lambda: {"vibe_scheduler": "ok"},
        )
        monkeypatch.setattr(
            "trade_integrations.auto_paper.mcp_actions.start_auto_paper",
            lambda **k: None,
        )
        monkeypatch.setattr(
            "trade_integrations.autonomous_agents.infra_startup.start_required_infra",
            lambda **k: ([], []),
        )

        orch_sid = "orch_flow3"
        first = proposals.propose_autonomous_agent(
            symbols=["NIFTY"],
            name="First",
            mandate="Paper",
            orchestrator_session_id=orch_sid,
        )
        second = proposals.propose_autonomous_agent(
            symbols=["BANKNIFTY"],
            name="Second",
            mandate="Paper options",
            allowed_instruments=["options"],
            orchestrator_session_id=orch_sid,
        )
        first_id = first["proposal_id"]
        second_id = second["proposal_id"]

        class FakeSession:
            def __init__(self, sid):
                self.session_id = sid

        class FakeStore:
            def __init__(self):
                from src.session.models import Session
                from src.session.orchestrator_profile import SESSION_KIND_ORCHESTRATOR

                self.session = Session(
                    session_id=orch_sid,
                    title="autonomous:orchestrator",
                    config={"session_kind": SESSION_KIND_ORCHESTRATOR},
                )

            def update_session(self, session):
                self.session = session

            def append_message(self, msg):
                pass

        class FakeSvc:
            def __init__(self):
                self.store = FakeStore()

            def get_session(self, sid):
                from src.session.models import Session
                from src.session.orchestrator_profile import SESSION_KIND_ORCHESTRATOR

                if sid != orch_sid:
                    return None
                return Session(
                    session_id=orch_sid,
                    title="autonomous:orchestrator",
                    config={"session_kind": SESSION_KIND_ORCHESTRATOR},
                )

            def create_session(self, title="", config=None):
                return FakeSession("should_not_create")

            class Bus:
                def emit(self, *a, **k):
                    pass

            event_bus = Bus()

        svc = FakeSvc()

        with pytest.raises(ValueError, match="superseded"):
            proposals.commit_autonomous_agent(
                proposal_id=first_id,
                consent_ack=True,
                session_service=svc,
                orchestrator_session_id=orch_sid,
            )

        result = proposals.commit_autonomous_agent(
            proposal_id=second_id,
            consent_ack=True,
            session_service=svc,
            orchestrator_session_id=orch_sid,
        )
        assert result["status"] == "ok"
        assert load_proposal(first_id).get("superseded") is True
