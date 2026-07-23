"""Tests for orchestrator session create behavior — always fresh draft."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi import HTTPException

AGENT_SRC = Path(__file__).resolve().parents[1] / "vibetrading" / "agent"
if str(AGENT_SRC) not in sys.path:
    sys.path.insert(0, str(AGENT_SRC))


def test_orchestrator_session_always_creates_fresh_draft(monkeypatch, tmp_path):
    from src.api import autonomous_routes as routes

    hub = tmp_path / "hub"
    (hub / "_data" / "autonomous_agents").mkdir(parents=True)
    monkeypatch.setattr(
        "trade_integrations.context.hub.get_hub_dir",
        lambda: hub,
    )

    class FakeSession:
        def __init__(self, sid):
            self.session_id = sid
            self.title = "autonomous:draft:aa_test"
            self.config = {
                "session_kind": "autonomous_orchestrator",
                "orchestrator": True,
                "draft_agent_id": "aa_test",
            }

    created = []

    class FakeSvc:
        def __init__(self):
            self.n = 0

        def create_session(self, title="", config=None):
            self.n += 1
            s = FakeSession(f"sess_{self.n}")
            created.append(s)
            return s

    fake = FakeSvc()
    monkeypatch.setattr(routes, "_session_service", lambda: fake)

    r1 = routes.get_or_create_orchestrator_session()
    r2 = routes.get_or_create_orchestrator_session()
    assert r1.session_id != r2.session_id
    assert len(created) == 2


def test_create_draft_route_returns_agent_and_session(monkeypatch, tmp_path):
    from src.api import autonomous_routes as routes

    hub = tmp_path / "hub"
    (hub / "_data" / "autonomous_agents").mkdir(parents=True)
    monkeypatch.setattr(
        "trade_integrations.context.hub.get_hub_dir",
        lambda: hub,
    )

    class FakeSession:
        def __init__(self, sid):
            self.session_id = sid
            self.title = "autonomous:draft:aa_route"
            self.config = {"draft_agent_id": "aa_route"}

    class FakeSvc:
        def create_session(self, title="", config=None):
            return FakeSession("sess_route_1")

    monkeypatch.setattr(routes, "_session_service", lambda: FakeSvc())

    resp = routes.create_draft_agent_route()
    assert resp.agent_id.startswith("aa_")
    assert resp.session_id == "sess_route_1"
    assert resp.agent["status"] == "draft"


def test_drafts_get_returns_405():
    from src.api.autonomous_routes import drafts_get_not_allowed

    with pytest.raises(HTTPException) as exc_info:
        drafts_get_not_allowed()
    assert exc_info.value.status_code == 405
    assert exc_info.value.detail["error"] == "method_not_allowed"


def test_post_drafts_returns_backfill_instead_of_second_create(monkeypatch, tmp_path):
    from src.api import autonomous_routes as routes

    hub = tmp_path / "hub"
    (hub / "_data" / "autonomous_agents").mkdir(parents=True)
    monkeypatch.setattr(
        "trade_integrations.context.hub.get_hub_dir",
        lambda: hub,
    )

    backfill_payload = {
        "agent_id": "aa_backfill",
        "session_id": "sess_backfill",
        "agent": {
            "id": "aa_backfill",
            "status": "draft",
            "vibe_session_id": "sess_backfill",
        },
        "backfilled": True,
    }
    create_calls = {"n": 0}

    def fake_backfill(**kwargs):
        return backfill_payload

    def fake_create(**kwargs):
        create_calls["n"] += 1
        raise AssertionError("create_draft_agent should not run when backfill succeeds")

    monkeypatch.setattr(
        "trade_integrations.autonomous_agents.store.backfill_orphan_orchestrator_session",
        fake_backfill,
    )
    monkeypatch.setattr(
        "trade_integrations.autonomous_agents.store.create_draft_agent",
        fake_create,
    )
    monkeypatch.setattr(routes, "_session_service", lambda: object())

    resp = routes.create_draft_agent_route()
    assert resp.agent_id == "aa_backfill"
    assert resp.session_id == "sess_backfill"
    assert create_calls["n"] == 0
