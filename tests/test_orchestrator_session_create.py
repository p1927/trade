"""Tests for orchestrator session get-or-create behavior."""

from __future__ import annotations

import sys
from pathlib import Path

AGENT_SRC = Path(__file__).resolve().parents[1] / "vibetrading" / "agent"
if str(AGENT_SRC) not in sys.path:
    sys.path.insert(0, str(AGENT_SRC))


def test_orchestrator_session_reuses_active_session(monkeypatch, tmp_path):
    from vibetrading.agent.src.api import autonomous_routes as routes

    hub = tmp_path / "hub"
    (hub / "_data" / "autonomous_agents").mkdir(parents=True)
    monkeypatch.setattr(
        "trade_integrations.context.hub.get_hub_dir",
        lambda: hub,
    )

    class FakeSession:
        def __init__(self, sid):
            self.session_id = sid
            self.title = "autonomous:orchestrator"
            self.config = {
                "session_kind": "autonomous_orchestrator",
                "orchestrator": True,
            }

    created = []

    class FakeSvc:
        def get_session(self, sid):
            if sid == "orch_reuse_1":
                return FakeSession(sid)
            return None

        def create_session(self, title="", config=None):
            s = FakeSession(f"orch_new_{len(created) + 1}")
            created.append(s)
            return s

    fake = FakeSvc()
    monkeypatch.setattr(routes, "_session_service", lambda: fake)

    from trade_integrations.autonomous_agents.store import set_active_orchestrator_session_id

    set_active_orchestrator_session_id("orch_reuse_1")

    r1 = routes.get_or_create_orchestrator_session()
    r2 = routes.get_or_create_orchestrator_session()
    assert r1.session_id == "orch_reuse_1"
    assert r2.session_id == "orch_reuse_1"
    assert created == []


def test_orchestrator_session_creates_when_no_active(monkeypatch, tmp_path):
    from vibetrading.agent.src.api import autonomous_routes as routes

    hub = tmp_path / "hub"
    (hub / "_data" / "autonomous_agents").mkdir(parents=True)
    monkeypatch.setattr(
        "trade_integrations.context.hub.get_hub_dir",
        lambda: hub,
    )

    class FakeSession:
        def __init__(self, i):
            self.session_id = f"orch_new_{i}"
            self.title = "autonomous:orchestrator"
            self.config = {
                "session_kind": "autonomous_orchestrator",
                "orchestrator": True,
            }

    class FakeSvc:
        def __init__(self):
            self.n = 0
            self.created = []
            self.sessions = {}

        def get_session(self, sid):
            return self.sessions.get(sid)

        def create_session(self, title="", config=None):
            self.n += 1
            s = FakeSession(self.n)
            self.sessions[s.session_id] = s
            self.created.append(s)
            return s

    fake = FakeSvc()
    monkeypatch.setattr(routes, "_session_service", lambda: fake)

    r1 = routes.get_or_create_orchestrator_session()
    r2 = routes.get_or_create_orchestrator_session()
    assert r1.session_id == r2.session_id == "orch_new_1"
    assert len(fake.created) == 1
