"""Draft autonomous agent lifecycle: create, commit, delete, teardown."""

from __future__ import annotations

import json
import sys
import time
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest

AGENT_SRC = Path(__file__).resolve().parents[1] / "vibetrading" / "agent"
if str(AGENT_SRC) not in sys.path:
    sys.path.insert(0, str(AGENT_SRC))


@pytest.fixture
def agents_hub(tmp_path, monkeypatch):
    hub = tmp_path / "hub"
    (hub / "_data" / "autonomous_agents" / "proposals").mkdir(parents=True)
    (hub / "_data" / "auto_paper" / "sessions").mkdir(parents=True)
    monkeypatch.setattr(
        "trade_integrations.context.hub.get_hub_dir",
        lambda: hub,
    )
    return hub


class FakeSession:
    def __init__(self, sid: str, *, config: dict | None = None, title: str = ""):
        self.session_id = sid
        self.title = title or sid
        self.config = dict(config or {})


class FakeSessionService:
    def __init__(self) -> None:
        self.sessions: dict[str, FakeSession] = {}
        self.deleted: list[str] = []
        self.created = 0

    def create_session(self, title="", config=None):
        self.created += 1
        sid = f"sess_{uuid.uuid4().hex[:12]}"
        session = FakeSession(sid, config=config or {}, title=title)
        self.sessions[sid] = session
        return session

    def get_session(self, sid):
        return self.sessions.get(str(sid))

    def delete_session(self, sid):
        sid = str(sid)
        if sid in self.sessions:
            del self.sessions[sid]
            self.deleted.append(sid)
            return True
        return False


def _ready_proposal(*, proposal_id: str, orch_sid: str, draft_agent_id: str) -> dict:
    return {
        "proposal_id": proposal_id,
        "status": "ready",
        "missing_fields": [],
        "routing_errors": [],
        "symbols": ["NIFTY"],
        "execution_market": "IN",
        "execution_backend": "openalgo",
        "name": "NIFTY bot",
        "mandate": "paper trade",
        "constraints": {
            "mode": "paper",
            "budget_inr": 20000,
            "max_daily_loss_inr": 2000,
            "confidence_threshold": 75,
        },
        "mandate_config": {"allowed_instruments": ["options"]},
        "watch_spec": {"rules": [{"symbol": "NIFTY", "exchange": "IN"}]},
        "schedules": {"watch_ms": 420000, "research_ms": 5400000},
        "alert_rules": {},
        "orchestrator_session_id": orch_sid,
        "draft_agent_id": draft_agent_id,
        "expires_at_ms": int(time.time() * 1000) + 3_600_000,
    }


@pytest.mark.unit
def test_create_draft_agent(agents_hub) -> None:
    from trade_integrations.autonomous_agents.store import create_draft_agent, get_agent

    svc = FakeSessionService()
    result = create_draft_agent(session_service=svc)
    agent = get_agent(result["agent_id"])
    assert agent is not None
    assert agent["status"] == "draft"
    assert agent["vibe_session_id"] == result["session_id"]
    assert svc.sessions[result["session_id"]].config.get("draft_agent_id") == result["agent_id"]


@pytest.mark.unit
def test_commit_promotes_same_draft_id(agents_hub, monkeypatch) -> None:
    from trade_integrations.autonomous_agents import proposals
    from trade_integrations.autonomous_agents.store import create_draft_agent, get_agent, list_agents, save_proposal

    svc = FakeSessionService()
    draft = create_draft_agent(session_service=svc)
    agent_id = draft["agent_id"]
    orch_sid = draft["session_id"]
    proposal_id = "aap_draft_commit"
    save_proposal(_ready_proposal(proposal_id=proposal_id, orch_sid=orch_sid, draft_agent_id=agent_id))

    monkeypatch.setattr(
        "trade_integrations.autonomous_agents.infra_startup.start_required_infra",
        lambda **k: ([], []),
    )
    monkeypatch.setattr(
        "trade_integrations.autonomous_agents.session_promotion.promote_orchestrator_session",
        lambda **k: None,
    )

    result = proposals.commit_autonomous_agent(
        proposal_id=proposal_id,
        consent_ack=True,
        session_service=svc,
        orchestrator_session_id=orch_sid,
    )
    assert result["agent"]["id"] == agent_id
    assert result["agent"]["status"] == "running"
    assert len(list_agents()) == 1
    assert get_agent(agent_id) is not None
    promoted = get_agent(agent_id)
    assert promoted is not None
    assert promoted["status"] == "running"


@pytest.mark.unit
def test_delete_draft_teardown(agents_hub) -> None:
    from trade_integrations.autonomous_agents.proposals import delete_autonomous_agent
    from trade_integrations.autonomous_agents.store import create_draft_agent, get_agent, save_proposal

    svc = FakeSessionService()
    draft = create_draft_agent(session_service=svc)
    agent_id = draft["agent_id"]
    orch_sid = draft["session_id"]
    save_proposal(
        {
            "proposal_id": "aap_draft_del",
            "status": "ready",
            "orchestrator_session_id": orch_sid,
            "draft_agent_id": agent_id,
            "symbols": ["NIFTY"],
            "created_at": "2026-07-23T10:00:00Z",
            "expires_at_ms": int(time.time() * 1000) + 60_000,
        }
    )

    result = delete_autonomous_agent(agent_id, session_service=svc)
    assert result["status"] == "ok"
    assert get_agent(agent_id) is None
    assert orch_sid in svc.deleted
    proposal_path = agents_hub / "_data" / "autonomous_agents" / "proposals" / "aap_draft_del.json"
    assert not proposal_path.is_file()


@pytest.mark.unit
def test_max_concurrent_ignores_drafts(agents_hub, monkeypatch) -> None:
    from trade_integrations.autonomous_agents import proposals
    from trade_integrations.autonomous_agents.defaults import MAX_CONCURRENT_AGENTS
    from trade_integrations.autonomous_agents.store import create_draft_agent, save_agent, save_proposal

    svc = FakeSessionService()
    for i in range(MAX_CONCURRENT_AGENTS):
        save_agent(
            {
                "id": f"aa_running_{i}",
                "status": "running",
                "symbols": ["NIFTY"],
                "created_at": "2026-07-23T10:00:00Z",
            }
        )
    draft = create_draft_agent(session_service=svc)
    proposal_id = "aap_at_capacity"
    save_proposal(
        _ready_proposal(
            proposal_id=proposal_id,
            orch_sid=draft["session_id"],
            draft_agent_id=draft["agent_id"],
        )
    )
    monkeypatch.setattr(
        "trade_integrations.autonomous_agents.infra_startup.start_required_infra",
        lambda **k: ([], []),
    )
    monkeypatch.setattr(
        "trade_integrations.autonomous_agents.session_promotion.promote_orchestrator_session",
        lambda **k: None,
    )

    with pytest.raises(ValueError, match="max concurrent agents"):
        proposals.commit_autonomous_agent(
            proposal_id=proposal_id,
            consent_ack=True,
            session_service=svc,
            orchestrator_session_id=draft["session_id"],
        )


@pytest.mark.unit
def test_stop_autonomous_agent_uses_per_agent_stop(agents_hub, monkeypatch) -> None:
    from trade_integrations.autonomous_agents.proposals import stop_autonomous_agent
    from trade_integrations.autonomous_agents.store import save_agent

    agent_id = "aa_paper_stop"
    save_agent(
        {
            "id": agent_id,
            "status": "running",
            "symbols": ["NIFTY"],
            "created_at": "2026-07-23T10:00:00Z",
        }
    )
    calls: list[str] = []

    def fake_load_session(*, autonomous_agent_id=None):
        return {"enabled": True, "autonomous_agent_id": autonomous_agent_id}

    def fake_stop_session(*, autonomous_agent_id=None):
        calls.append(str(autonomous_agent_id))
        return {"enabled": False}

    monkeypatch.setattr(
        "trade_integrations.auto_paper.session_store.load_session",
        fake_load_session,
    )
    monkeypatch.setattr(
        "trade_integrations.auto_paper.session_store.stop_session",
        fake_stop_session,
    )

    stop_autonomous_agent(agent_id)
    assert calls == [agent_id]


@pytest.mark.unit
def test_delete_active_blocks_open_positions(agents_hub, monkeypatch) -> None:
    from trade_integrations.autonomous_agents.proposals import delete_autonomous_agent
    from trade_integrations.autonomous_agents.store import save_agent
    from trade_integrations.autonomous_agents.teardown import (
        AgentPositionSnapshot,
        OpenPositionsConflictError,
    )

    agent_id = "aa_with_positions"
    agent = {
        "id": agent_id,
        "status": "running",
        "symbols": ["NIFTY"],
        "vibe_session_id": "sess_active",
        "created_at": "2026-07-23T10:00:00Z",
    }
    save_agent(agent)
    monkeypatch.setattr(
        "trade_integrations.autonomous_agents.teardown.snapshot_agent_positions",
        lambda _agent: AgentPositionSnapshot(
            openalgo_rows=[{"symbol": "NIFTY24JUL25000CE", "quantity": 1}],
            alpaca_symbols=[],
            lookup_ok=True,
        ),
    )

    with pytest.raises(OpenPositionsConflictError) as exc_info:
        delete_autonomous_agent(agent_id, flatten_positions=False)
    assert exc_info.value.count == 1
    assert exc_info.value.openalgo_count == 1


@pytest.mark.unit
def test_delete_active_lookup_failure_blocks_without_flatten(agents_hub, monkeypatch) -> None:
    from trade_integrations.autonomous_agents.proposals import delete_autonomous_agent
    from trade_integrations.autonomous_agents.store import save_agent
    from trade_integrations.autonomous_agents.teardown import AgentPositionSnapshot, OpenPositionsLookupError

    agent_id = "aa_lookup_fail"
    save_agent(
        {
            "id": agent_id,
            "status": "running",
            "symbols": ["NIFTY"],
            "vibe_session_id": "sess_lookup",
            "created_at": "2026-07-23T10:00:00Z",
        }
    )
    monkeypatch.setattr(
        "trade_integrations.autonomous_agents.teardown.snapshot_agent_positions",
        lambda _agent: AgentPositionSnapshot(
            openalgo_rows=[],
            alpaca_symbols=[],
            lookup_ok=False,
            lookup_error="openalgo unreachable",
        ),
    )

    with pytest.raises(OpenPositionsLookupError, match="openalgo unreachable"):
        delete_autonomous_agent(agent_id, flatten_positions=False)


@pytest.mark.unit
def test_delete_active_lookup_failure_with_flatten_still_blocks(agents_hub, monkeypatch) -> None:
    from trade_integrations.autonomous_agents.proposals import delete_autonomous_agent
    from trade_integrations.autonomous_agents.store import save_agent
    from trade_integrations.autonomous_agents.teardown import AgentPositionSnapshot, OpenPositionsLookupError

    agent_id = "aa_lookup_flatten"
    save_agent(
        {
            "id": agent_id,
            "status": "running",
            "symbols": ["NIFTY"],
            "vibe_session_id": "sess_lookup_flatten",
            "created_at": "2026-07-23T10:00:00Z",
        }
    )
    monkeypatch.setattr(
        "trade_integrations.autonomous_agents.teardown.snapshot_agent_positions",
        lambda _agent: AgentPositionSnapshot(
            openalgo_rows=[],
            alpaca_symbols=[],
            lookup_ok=False,
            lookup_error="openalgo unreachable",
        ),
    )

    with pytest.raises(OpenPositionsLookupError, match="openalgo unreachable"):
        delete_autonomous_agent(agent_id, flatten_positions=True)


@pytest.mark.unit
def test_us_snapshot_fails_when_openalgo_lookup_fails(agents_hub, monkeypatch) -> None:
    from trade_integrations.autonomous_agents.teardown import snapshot_agent_positions

    agent = {
        "id": "aa_us_openalgo_down",
        "status": "running",
        "symbols": ["AAPL"],
        "execution_market": "US",
    }

    monkeypatch.setattr(
        "trade_integrations.autonomous_agents.teardown._openalgo_rows_for_agent",
        lambda _aid: ([], "openalgo down"),
    )

    snapshot = snapshot_agent_positions(agent)
    assert snapshot.lookup_ok is False
    assert snapshot.lookup_error == "openalgo down"
    assert snapshot.alpaca_symbols == []
    assert snapshot.total_open == 0


@pytest.mark.unit
def test_delete_active_flatten_incomplete_raises(agents_hub, monkeypatch) -> None:
    from trade_integrations.autonomous_agents.proposals import delete_autonomous_agent
    from trade_integrations.autonomous_agents.store import save_agent
    from trade_integrations.autonomous_agents.teardown import AgentPositionSnapshot, FlattenIncompleteError

    agent_id = "aa_flatten_partial"
    save_agent(
        {
            "id": agent_id,
            "status": "running",
            "symbols": ["NIFTY"],
            "vibe_session_id": "sess_flatten",
            "created_at": "2026-07-23T10:00:00Z",
        }
    )
    monkeypatch.setattr(
        "trade_integrations.autonomous_agents.teardown.snapshot_agent_positions",
        lambda _agent: AgentPositionSnapshot(
            openalgo_rows=[{"symbol": "NIFTY24JUL25000CE", "quantity": 1}],
            alpaca_symbols=[],
            lookup_ok=True,
        ),
    )
    monkeypatch.setattr(
        "trade_integrations.autonomous_agents.teardown.flatten_agent_positions",
        lambda _agent: {
            "remaining_positions": 1,
            "openalgo_remaining": 1,
            "alpaca_remaining": 0,
            "status": "partial",
        },
    )

    with pytest.raises(FlattenIncompleteError) as exc_info:
        delete_autonomous_agent(agent_id, flatten_positions=True)
    assert exc_info.value.openalgo_remaining == 1


@pytest.mark.unit
def test_draft_lifecycle_mutations_rejected(agents_hub) -> None:
    from trade_integrations.autonomous_agents.proposals import (
        pause_autonomous_agent,
        resume_autonomous_agent,
        stop_autonomous_agent,
    )
    from trade_integrations.autonomous_agents.store import create_draft_agent

    svc = FakeSessionService()
    draft = create_draft_agent(session_service=svc)
    agent_id = draft["agent_id"]

    with pytest.raises(ValueError, match="draft agents cannot be stopped"):
        stop_autonomous_agent(agent_id)
    with pytest.raises(ValueError, match="draft agents cannot be paused"):
        pause_autonomous_agent(agent_id)
    with pytest.raises(ValueError, match="draft agents cannot be resumed"):
        resume_autonomous_agent(agent_id)


@pytest.mark.unit
def test_delete_proposals_scans_after_explicit_proposal_id(agents_hub) -> None:
    from trade_integrations.autonomous_agents.teardown import delete_proposals_for_agent
    from trade_integrations.autonomous_agents.store import save_proposal

    orch_sid = "sess_proposal_scan"
    agent_id = "aa_proposal_scan"
    explicit = "aap_explicit"
    orphan = "aap_orphan_same_session"
    save_proposal(
        {
            "proposal_id": explicit,
            "status": "ready",
            "orchestrator_session_id": orch_sid,
            "draft_agent_id": agent_id,
            "symbols": ["NIFTY"],
            "created_at": "2026-07-23T10:00:00Z",
            "expires_at_ms": int(time.time() * 1000) + 60_000,
        }
    )
    save_proposal(
        {
            "proposal_id": orphan,
            "status": "ready",
            "orchestrator_session_id": orch_sid,
            "draft_agent_id": agent_id,
            "symbols": ["NIFTY"],
            "created_at": "2026-07-23T10:00:00Z",
            "expires_at_ms": int(time.time() * 1000) + 60_000,
        }
    )

    removed = delete_proposals_for_agent(
        vibe_session_id=orch_sid,
        draft_agent_id=agent_id,
        proposal_id=explicit,
    )
    assert removed == 2
    assert not (agents_hub / "_data" / "autonomous_agents" / "proposals" / f"{explicit}.json").is_file()
    assert not (agents_hub / "_data" / "autonomous_agents" / "proposals" / f"{orphan}.json").is_file()


@pytest.mark.unit
def test_snapshot_includes_openalgo_rows_for_us_agent(agents_hub, monkeypatch) -> None:
    from trade_integrations.autonomous_agents.teardown import snapshot_agent_positions

    agent = {
        "id": "aa_us_positions",
        "status": "running",
        "symbols": ["AAPL", "MSFT"],
        "execution_market": "US",
    }

    monkeypatch.setattr(
        "trade_integrations.autonomous_agents.teardown._openalgo_rows_for_agent",
        lambda _aid: ([{"symbol": "AAPL", "qty": 1}], None),
    )

    snapshot = snapshot_agent_positions(agent)
    assert snapshot.lookup_ok is True
    assert snapshot.openalgo_rows == [{"symbol": "AAPL", "qty": 1}]
    assert snapshot.alpaca_symbols == []
    assert snapshot.total_open == 1


@pytest.mark.unit
def test_backfill_orphan_orchestrator_session(agents_hub, monkeypatch) -> None:
    from trade_integrations.autonomous_agents.store import (
        backfill_orphan_orchestrator_session,
        get_agent,
        set_active_orchestrator_session_id,
    )

    svc = FakeSessionService()
    session = svc.create_session(
        title="autonomous:orchestrator",
        config={"session_kind": "autonomous_orchestrator", "orchestrator": True},
    )
    set_active_orchestrator_session_id(session.session_id)

    monkeypatch.setattr(
        "trade_integrations.autonomous_agents.store.find_agent_by_vibe_session",
        lambda sid, status=None: None,
    )

    result = backfill_orphan_orchestrator_session(session_service=svc)
    assert result is not None
    agent = get_agent(result["agent_id"])
    assert agent is not None
    assert agent["status"] == "draft"
    assert agent["vibe_session_id"] == session.session_id
