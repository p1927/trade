"""Infra pause + heal for autonomous agents."""

from __future__ import annotations

import sys
import time
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


def _ready_proposal(proposal_id: str = "aap_infra1"):
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
        "expires_at_ms": int(time.time() * 1000) + 3_600_000,
    }


class FakeSvc:
    def create_session(self, title="", config=None):
        from types import SimpleNamespace

        return SimpleNamespace(session_id="sess_infra", title=title)

    class Bus:
        def emit(self, *a, **k):
            pass

    event_bus = Bus()


@pytest.mark.unit
def test_commit_pauses_on_infra_failure(agents_hub, monkeypatch):
    from trade_integrations.autonomous_agents import proposals
    from trade_integrations.autonomous_agents.store import save_proposal

    save_proposal(_ready_proposal())
    monkeypatch.setattr(
        "trade_integrations.autonomous_agents.infra_startup.start_required_infra",
        lambda **k: (["propose_autonomous_agent failed: down"], []),
    )

    result = proposals.commit_autonomous_agent(
        proposal_id="aap_infra1",
        consent_ack=True,
        session_service=FakeSvc(),
    )
    agent = result["agent"]
    assert result.get("infra_paused") is True
    assert agent["status"] == "paused"
    assert agent["pause_reason"] == "infra"
    assert agent["infra_pending"]


@pytest.mark.unit
def test_infra_heal_resumes_agent(agents_hub, monkeypatch):
    from trade_integrations.autonomous_agents import proposals
    from trade_integrations.autonomous_agents.infra_startup import attempt_infra_heal
    from trade_integrations.autonomous_agents.store import get_agent, save_agent, save_proposal

    save_proposal(_ready_proposal("aap_infra2"))
    monkeypatch.setattr(
        "trade_integrations.autonomous_agents.infra_startup.start_required_infra",
        lambda **k: (["propose_autonomous_agent failed: down"], []),
    )
    result = proposals.commit_autonomous_agent(
        proposal_id="aap_infra2",
        consent_ack=True,
        session_service=FakeSvc(),
    )
    agent_id = result["agent"]["id"]

    monkeypatch.setattr(
        "trade_integrations.autonomous_agents.infra_startup.start_required_infra",
        lambda **k: ([], []),
    )
    agent = get_agent(agent_id)
    assert agent is not None
    agent["infra_last_attempt_at"] = None
    save_agent(agent)

    healed = attempt_infra_heal(agent_id)
    assert healed is not None
    assert healed["status"] == "running"
    assert healed.get("pause_reason") is None
    assert get_agent(agent_id)["status"] == "running"


@pytest.mark.unit
def test_start_required_infra_defers_nautilus_until_plan_approved(agents_hub, monkeypatch):
    from trade_integrations.autonomous_agents.infra_startup import start_required_infra
    from trade_integrations.execution.profile import resolve_profile

    called: list[str] = []

    def _fake_ensure(agent_id: str):
        called.append(agent_id)
        return f"Nautilus watch not started — no active watches for {agent_id}"

    monkeypatch.setattr(
        "trade_integrations.autonomous_agents.nautilus_watch.ensure_nautilus_watch_for_agent",
        _fake_ensure,
    )

    agent = {
        "id": "aa_defer_nautilus",
        "symbols": ["NIFTY"],
        "constraints": {"mode": "paper"},
        "execution_market": "IN",
        "execution_backend": "openalgo",
    }
    profile = resolve_profile(agent=agent)
    blocking, warnings = start_required_infra(
        agent=agent,
        profile=profile,
        proposal={"constraints": agent["constraints"]},
        primary_symbol="NIFTY",
        symbols=["NIFTY"],
        vibe_session_id="sess_defer",
        fresh_mandate_cfg=None,
    )

    assert called == []
    assert blocking == []
    assert warnings == []


@pytest.mark.unit
def test_start_required_infra_blocks_nautilus_when_plan_approved(agents_hub, monkeypatch):
    from trade_integrations.autonomous_agents.infra_startup import start_required_infra
    from trade_integrations.execution.profile import resolve_profile

    monkeypatch.setattr(
        "trade_integrations.autonomous_agents.nautilus_watch.ensure_nautilus_watch_for_agent",
        lambda agent_id: f"Nautilus watch not started — no active watches for {agent_id}",
    )

    agent = {
        "id": "aa_need_nautilus",
        "symbols": ["NIFTY"],
        "constraints": {"mode": "paper"},
        "execution_market": "IN",
        "execution_backend": "openalgo",
        "plan_approved_at": "2026-07-16T20:00:00+00:00",
    }
    profile = resolve_profile(agent=agent)
    blocking, warnings = start_required_infra(
        agent=agent,
        profile=profile,
        proposal={"constraints": agent["constraints"]},
        primary_symbol="NIFTY",
        symbols=["NIFTY"],
        vibe_session_id="sess_need",
        fresh_mandate_cfg=None,
    )

    assert len(blocking) == 1
    assert "no active watches" in blocking[0]
    assert warnings == []


@pytest.mark.unit
def test_start_required_infra_ensures_registry_when_plan_approved(agents_hub, monkeypatch):
    from trade_integrations.autonomous_agents.infra_startup import start_required_infra
    from trade_integrations.execution.profile import resolve_profile

    ensured: list[str] = []

    def _fake_ensure(agent_id: str):
        ensured.append(agent_id)

    monkeypatch.setattr(
        "trade_integrations.autonomous_agents.infra_startup._ensure_registry_watch",
        _fake_ensure,
    )
    monkeypatch.setattr(
        "trade_integrations.autonomous_agents.nautilus_watch.ensure_nautilus_watch_for_agent",
        lambda agent_id: None,
    )

    agent = {
        "id": "aa_registry_ensure",
        "symbols": ["NIFTY"],
        "constraints": {"mode": "paper"},
        "execution_market": "IN",
        "execution_backend": "openalgo",
        "plan_approved_at": "2026-07-16T20:00:00+00:00",
    }
    profile = resolve_profile(agent=agent)
    blocking, warnings = start_required_infra(
        agent=agent,
        profile=profile,
        proposal={"constraints": agent["constraints"]},
        primary_symbol="NIFTY",
        symbols=["NIFTY"],
        vibe_session_id="sess_registry",
        fresh_mandate_cfg=None,
    )

    assert ensured == ["aa_registry_ensure"]
    assert blocking == []
    assert warnings == []
