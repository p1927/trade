"""Tests for orchestrator session always creating fresh sessions."""

from __future__ import annotations

import sys
from pathlib import Path

AGENT_SRC = Path(__file__).resolve().parents[1] / "vibetrading" / "agent"
if str(AGENT_SRC) not in sys.path:
    sys.path.insert(0, str(AGENT_SRC))


def test_orchestrator_session_always_creates_new(monkeypatch):
    from vibetrading.agent.src.api import autonomous_routes as routes

    class FakeSession:
        def __init__(self, i):
            self.session_id = f"orch_new_{i}"
            self.title = "autonomous:orchestrator"

    class FakeSvc:
        def __init__(self):
            self.n = 0

        def get_session(self, sid):
            return None  # never reuse

        def create_session(self, title="", config=None):
            self.n += 1
            return FakeSession(self.n)

    fake = FakeSvc()
    monkeypatch.setattr(routes, "_session_service", lambda: fake)

    r1 = routes.get_or_create_orchestrator_session()
    r2 = routes.get_or_create_orchestrator_session()
    assert r1.session_id != r2.session_id
    assert r1.session_id == "orch_new_1"
    assert r2.session_id == "orch_new_2"
