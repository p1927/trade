"""Tests for autonomous proposal session_id persistence."""

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


@pytest.mark.unit
def test_propose_persists_session_id(agents_hub, monkeypatch):
    from trade_integrations.autonomous_agents import proposals
    from trade_integrations.autonomous_agents.store import load_proposal

    monkeypatch.setattr(proposals, "build_stack_health", lambda: {"vibe_scheduler": "ok"})

    result = proposals.propose_autonomous_agent(
        symbols=["NIFTY"],
        name="NIFTY bot",
        mandate="Paper trade",
        orchestrator_session_id="orch_sess_1",
    )
    saved = load_proposal(result["proposal_id"])
    assert saved is not None
    assert saved.get("session_id") == "orch_sess_1"
    assert saved.get("orchestrator_session_id") == "orch_sess_1"


@pytest.mark.unit
def test_load_latest_returns_session_id_without_mutating_file(agents_hub, monkeypatch):
    from trade_integrations.autonomous_agents.store import load_latest_proposal_for_orchestrator, save_proposal

    save_proposal(
        {
            "proposal_id": "aap_latest",
            "orchestrator_session_id": "orch_x",
            "status": "ready",
            "created_at": "2026-07-17T12:00:00+00:00",
            "expires_at_ms": 9_999_999_999_999,
            "symbols": ["NIFTY"],
        }
    )
    from trade_integrations.autonomous_agents.store import _proposal_path

    latest = load_latest_proposal_for_orchestrator("orch_x")
    assert latest is not None
    assert latest.get("session_id") == "orch_x"

    import json

    raw = json.loads(_proposal_path("aap_latest").read_text(encoding="utf-8"))
    assert raw.get("session_id") is None
