"""Commit-time guards for autonomous agent proposals."""

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


def _ready_proposal(**overrides):
    base = {
        "proposal_id": "aap_guard_test",
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
    base.update(overrides)
    return base


class FakeSvc:
    def create_session(self, title="", config=None):
        from types import SimpleNamespace

        return SimpleNamespace(session_id="sess_new", title=title)

    class Bus:
        def emit(self, *a, **k):
            pass

    event_bus = Bus()


@pytest.mark.unit
def test_commit_rejects_incomplete_proposal(agents_hub):
    from trade_integrations.autonomous_agents import proposals
    from trade_integrations.autonomous_agents.store import save_proposal

    save_proposal(_ready_proposal(status="incomplete", missing_fields=["allowed_instruments"]))

    with pytest.raises(ValueError, match="not ready"):
        proposals.commit_autonomous_agent(
            proposal_id="aap_guard_test",
            consent_ack=True,
            session_service=FakeSvc(),
        )


@pytest.mark.unit
def test_commit_rejects_routing_errors(agents_hub):
    from trade_integrations.autonomous_agents import proposals
    from trade_integrations.autonomous_agents.store import save_proposal

    save_proposal(
        _ready_proposal(
            routing_errors=["Symbol NIFTY is India-listed but execution_market is US."],
            execution_market="US",
        )
    )

    with pytest.raises(ValueError, match="routing"):
        proposals.commit_autonomous_agent(
            proposal_id="aap_guard_test",
            consent_ack=True,
            session_service=FakeSvc(),
        )


@pytest.mark.unit
def test_commit_rejects_live_mode(agents_hub):
    from trade_integrations.autonomous_agents import proposals
    from trade_integrations.autonomous_agents.store import save_proposal

    save_proposal(
        _ready_proposal(
            constraints={
                "mode": "live",
                "budget_inr": 20000,
                "max_daily_loss_inr": 2000,
                "confidence_threshold": 75,
            }
        )
    )

    with pytest.raises(ValueError, match="live mode"):
        proposals.commit_autonomous_agent(
            proposal_id="aap_guard_test",
            consent_ack=True,
            session_service=FakeSvc(),
        )


@pytest.mark.unit
def test_commit_rejects_without_consent(agents_hub):
    from trade_integrations.autonomous_agents import proposals
    from trade_integrations.autonomous_agents.store import save_proposal

    save_proposal(_ready_proposal())

    with pytest.raises(ValueError, match="consent_ack"):
        proposals.commit_autonomous_agent(
            proposal_id="aap_guard_test",
            consent_ack=False,
            session_service=FakeSvc(),
        )


@pytest.mark.unit
def test_propose_rejects_live_mode(agents_hub, monkeypatch):
    from trade_integrations.autonomous_agents import proposals
    from trade_integrations.autonomous_agents.store import load_proposal

    monkeypatch.setattr(
        proposals,
        "build_stack_health",
        lambda: {"vibe_scheduler": "ok"},
    )

    result = proposals.propose_autonomous_agent(symbols=["NIFTY"], mode="live")
    assert result["status"] == "incomplete"
    assert "live mode" in str(result.get("routing_errors") or result.get("message") or "")
    saved = load_proposal(result["proposal_id"])
    assert saved is not None
    assert saved.get("status") == "incomplete"


@pytest.mark.unit
def test_commit_rejects_expired_proposal(agents_hub):
    from trade_integrations.autonomous_agents import proposals
    from trade_integrations.autonomous_agents.store import save_proposal

    save_proposal(_ready_proposal(expires_at_ms=int(time.time() * 1000) - 1))

    with pytest.raises(ValueError, match="expired"):
        proposals.commit_autonomous_agent(
            proposal_id="aap_guard_test",
            consent_ack=True,
            session_service=FakeSvc(),
        )


@pytest.mark.unit
def test_commit_rejects_max_concurrent(agents_hub, monkeypatch):
    from trade_integrations.autonomous_agents import proposals
    from trade_integrations.autonomous_agents.store import save_proposal

    monkeypatch.setattr(
        proposals,
        "MAX_CONCURRENT_AGENTS",
        1,
    )
    monkeypatch.setattr(
        proposals,
        "list_agents",
        lambda: [{"id": "aa_existing", "status": "running"}],
    )
    save_proposal(_ready_proposal(proposal_id="aap_max_test"))

    with pytest.raises(ValueError, match="max concurrent"):
        proposals.commit_autonomous_agent(
            proposal_id="aap_max_test",
            consent_ack=True,
            session_service=FakeSvc(),
        )
