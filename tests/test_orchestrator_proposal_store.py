"""Tests for latest orchestrator proposal lookup."""

from __future__ import annotations

import time

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


def test_load_latest_proposal_for_orchestrator(agents_hub) -> None:
    from trade_integrations.autonomous_agents.store import load_latest_proposal_for_orchestrator, save_proposal

    orch = "orch_session_abc"
    save_proposal(
        {
            "proposal_id": "aap_old",
            "status": "ready",
            "orchestrator_session_id": orch,
            "symbols": ["NIFTY"],
            "created_at": "2026-07-16T10:00:00Z",
            "expires_at_ms": int(time.time() * 1000) + 60_000,
        }
    )
    save_proposal(
        {
            "proposal_id": "aap_new",
            "status": "ready",
            "orchestrator_session_id": orch,
            "symbols": ["NIFTY"],
            "created_at": "2026-07-16T11:00:00Z",
            "expires_at_ms": int(time.time() * 1000) + 60_000,
        }
    )

    latest = load_latest_proposal_for_orchestrator(orch)
    assert latest is not None
    assert latest["proposal_id"] == "aap_new"
    assert latest.get("session_id") == orch
