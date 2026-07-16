"""Tests for proposal superseding in orchestrator sessions."""

from __future__ import annotations

import pytest

from trade_integrations.autonomous_agents.store import (
    load_latest_proposal_for_orchestrator,
    load_proposal,
    mark_superseded_proposals,
    save_proposal,
)


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
class TestProposalSupersede:
    def test_repropose_marks_prior_as_superseded(self, agents_hub):
        orch = "orch_sup1"
        save_proposal(
            {
                "proposal_id": "aap_first",
                "orchestrator_session_id": orch,
                "status": "ready",
                "symbols": ["NIFTY"],
                "expires_at_ms": 9999999999999,
                "created_at": "2026-07-16T10:00:00+00:00",
            }
        )
        save_proposal(
            {
                "proposal_id": "aap_second",
                "orchestrator_session_id": orch,
                "status": "ready",
                "symbols": ["BANKNIFTY"],
                "expires_at_ms": 9999999999999,
                "created_at": "2026-07-16T10:01:00+00:00",
            }
        )
        mark_superseded_proposals(orch, except_proposal_id="aap_second")

        first = load_proposal("aap_first")
        assert first is not None
        assert first.get("superseded") is True
        assert first.get("superseded_by") == "aap_second"

        latest = load_latest_proposal_for_orchestrator(orch)
        assert latest is not None
        assert latest.get("proposal_id") == "aap_second"
