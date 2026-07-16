"""Tests for orchestrator → agent session promotion."""

from __future__ import annotations

import sys
from pathlib import Path

AGENT_SRC = Path(__file__).resolve().parents[1] / "vibetrading" / "agent"
if str(AGENT_SRC) not in sys.path:
    sys.path.insert(0, str(AGENT_SRC))


def test_promote_orchestrator_session_updates_config_and_title(tmp_path, monkeypatch):
    from trade_integrations.autonomous_agents.session_promotion import promote_orchestrator_session
    from src.session.models import Session
    from src.session.orchestrator_profile import SESSION_KIND_ORCHESTRATOR

    class FakeStore:
        def __init__(self):
            self.session = Session(
                session_id="orch123",
                title="autonomous:orchestrator",
                config={"session_kind": SESSION_KIND_ORCHESTRATOR, "orchestrator": True},
            )
            self.messages = []

        def get_session(self, sid):
            return self.session if sid == "orch123" else None

        def update_session(self, session):
            self.session = session

        def append_message(self, msg):
            self.messages.append(msg)

    class FakeSvc:
        class Bus:
            def __init__(self, outer):
                self.outer = outer

            def emit(self, sid, event, payload):
                self.outer.events.append((sid, event, payload))

        def __init__(self):
            self.store = FakeStore()
            self.events = []
            self.event_bus = self.Bus(self)

        def get_session(self, sid):
            return self.store.get_session(sid)

    svc = FakeSvc()
    cfg = {
        "session_kind": "autonomous_agent",
        "autonomous_agent_id": "aa_abc",
        "symbols": ["NIFTY"],
    }
    out = promote_orchestrator_session(
        session_service=svc,
        orchestrator_session_id="orch123",
        agent_id="aa_abc",
        name="NIFTY autonomous",
        session_cfg=cfg,
    )
    assert out == "orch123"
    assert svc.store.session.config["session_kind"] == "autonomous_agent"
    assert svc.store.session.config["autonomous_agent_id"] == "aa_abc"
    assert svc.store.session.title == "autonomous:NIFTY autonomous"
    assert len(svc.store.messages) == 1
    assert "NIFTY autonomous" in svc.store.messages[0].content

    assert len(svc.events) == 2
    sid0, ev0, payload0 = svc.events[0]
    assert sid0 == "orch123"
    assert ev0 == "message.received"
    assert payload0["role"] == "system"
    assert payload0["message_id"] == svc.store.messages[0].message_id
    assert "NIFTY autonomous" in payload0["content"]

    sid1, ev1, payload1 = svc.events[1]
    assert sid1 == "orch123"
    assert ev1 == "session.promoted"
    assert payload1 == {
        "session_id": "orch123",
        "agent_id": "aa_abc",
        "session_kind": "autonomous_agent",
    }
