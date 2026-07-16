"""Tests for commit flow reusing orchestrator session via promotion."""

from __future__ import annotations

import pytest


@pytest.fixture
def agents_hub(tmp_path, monkeypatch):
    hub = tmp_path / "hub"
    (hub / "_data" / "autonomous_agents" / "proposals").mkdir(parents=True)
    monkeypatch.setattr(
        "trade_integrations.context.hub.get_hub_dir",
        lambda: hub,
    )
    return hub


def test_commit_reuses_orchestrator_session(monkeypatch, agents_hub):
    from trade_integrations.autonomous_agents import proposals
    from trade_integrations.autonomous_agents.store import save_proposal

    orch_sid = "orch_commit1"
    proposal_id = "aap_test1"
    save_proposal(
        {
            "proposal_id": proposal_id,
            "status": "ready",
            "symbols": ["NIFTY"],
            "name": "NIFTY bot",
            "mandate": "paper trade",
            "constraints": {
                "mode": "paper",
                "budget_inr": 20000,
                "max_daily_loss_inr": 2000,
                "confidence_threshold": 75,
            },
            "mandate_config": {},
            "watch_spec": {},
            "schedules": {"watch_ms": 420000, "research_ms": 5400000},
            "alert_rules": {},
            "orchestrator_session_id": orch_sid,
            "expires_at_ms": 9999999999999,
        }
    )

    created = []

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
                config={"session_kind": SESSION_KIND_ORCHESTRATOR, "orchestrator": True},
            )
            self.messages = []

        def update_session(self, session):
            self.session = session

        def append_message(self, msg):
            self.messages.append(msg)

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
                config={"session_kind": SESSION_KIND_ORCHESTRATOR, "orchestrator": True},
            )

        def create_session(self, title="", config=None):
            s = FakeSession(f"new_{len(created)}")
            created.append(s)
            return s

        class Bus:
            def emit(self, *a, **k):
                pass

        event_bus = Bus()

    svc = FakeSvc()
    monkeypatch.setattr(
        "trade_integrations.auto_paper.mcp_actions.start_auto_paper",
        lambda **k: None,
    )

    result = proposals.commit_autonomous_agent(
        proposal_id=proposal_id,
        consent_ack=True,
        session_service=svc,
        orchestrator_session_id=orch_sid,
    )
    assert result["vibe_session_id"] == orch_sid
    assert created == []  # must NOT create a new session
    assert result["agent"]["vibe_session_id"] == orch_sid
