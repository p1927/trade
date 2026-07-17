"""Atomic commit lock for autonomous agent proposals."""

from __future__ import annotations

import sys
import threading
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


def _ready_proposal(proposal_id: str):
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

        return SimpleNamespace(session_id="sess_atomic", title=title)

    class Bus:
        def emit(self, *a, **k):
            pass

    event_bus = Bus()


@pytest.mark.unit
def test_commit_lock_blocks_second_caller(agents_hub, monkeypatch):
    from trade_integrations.autonomous_agents import proposals
    from trade_integrations.autonomous_agents.store import acquire_proposal_commit_lock, save_proposal

    pid = "aap_atomic1"
    save_proposal(_ready_proposal(pid))

    monkeypatch.setattr(
        "trade_integrations.autonomous_agents.infra_startup.start_required_infra",
        lambda **k: ([], []),
    )

    with acquire_proposal_commit_lock(pid):
        with pytest.raises(ValueError, match="commit already in progress"):
            proposals.commit_autonomous_agent(
                proposal_id=pid,
                consent_ack=True,
                session_service=FakeSvc(),
            )


@pytest.mark.unit
def test_concurrent_commits_one_succeeds(agents_hub, monkeypatch):
    from trade_integrations.autonomous_agents import proposals
    from trade_integrations.autonomous_agents.store import list_agents, save_proposal

    pid = "aap_atomic2"
    save_proposal(_ready_proposal(pid))

    monkeypatch.setattr(
        "trade_integrations.autonomous_agents.infra_startup.start_required_infra",
        lambda **k: ([], []),
    )

    barrier = threading.Barrier(2)
    results: list[dict | Exception] = []

    def worker():
        barrier.wait()
        try:
            results.append(
                proposals.commit_autonomous_agent(
                    proposal_id=pid,
                    consent_ack=True,
                    session_service=FakeSvc(),
                )
            )
        except Exception as exc:
            results.append(exc)

    t1 = threading.Thread(target=worker)
    t2 = threading.Thread(target=worker)
    t1.start()
    t2.start()
    t1.join(timeout=10)
    t2.join(timeout=10)

    ok = [r for r in results if isinstance(r, dict) and r.get("status") == "ok"]
    errors = [r for r in results if isinstance(r, Exception)]
    assert len(ok) == 1
    assert len(errors) == 1
    assert len(list_agents()) == 1
