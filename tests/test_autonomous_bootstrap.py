"""Tests for bootstrap sequencing and research deferral."""

from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
INTEGRATIONS = ROOT / "integrations"
AGENT_SRC = ROOT / "vibetrading" / "agent"
if str(INTEGRATIONS) not in sys.path:
    sys.path.insert(0, str(INTEGRATIONS))
if str(AGENT_SRC) not in sys.path:
    sys.path.insert(0, str(AGENT_SRC))

from trade_integrations.autonomous_agents.bootstrap import finalize_bootstrap_if_ready  # noqa: E402
from trade_integrations.autonomous_agents.store import get_agent, save_agent  # noqa: E402
from trade_integrations.autonomous_agents.watch import _research_turn_recently_ran  # noqa: E402
from src.scheduled_research.autonomous_agent_jobs import register_agent_jobs  # noqa: E402
from src.scheduled_research.store import ScheduledResearchJobStore  # noqa: E402


@pytest.fixture
def hub_tmp(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    hub = tmp_path / "hub"
    hub.mkdir()
    monkeypatch.setenv("TRADE_STACK_HUB_DIR", str(hub))
    jobs_dir = tmp_path / "jobs"
    jobs_dir.mkdir()
    monkeypatch.setenv("SCHEDULED_RESEARCH_JOBS_DIR", str(jobs_dir))
    monkeypatch.setenv("AUTONOMOUS_AGENTS_ENABLE_SCHEDULER", "1")
    return hub


def _agent(agent_id: str = "aa_boot") -> dict:
    return {
        "id": agent_id,
        "type": "autonomous_agent.instance",
        "name": "Bootstrap Test",
        "status": "running",
        "symbols": ["NIFTY"],
        "execution_market": "IN",
        "constraints": {"mode": "paper"},
        "bootstrap_status": "running",
        "schedules": {"watch_ms": 420_000, "research_ms": 5_400_000},
        "vibe_session_id": "sess_boot",
    }


def test_finalize_bootstrap_requires_last_decision(hub_tmp: Path):
    save_agent(_agent())
    assert finalize_bootstrap_if_ready("aa_boot") is False
    agent = get_agent("aa_boot")
    assert agent["bootstrap_status"] == "running"


def test_finalize_bootstrap_auto_approves_and_activates_watch(hub_tmp: Path, monkeypatch: pytest.MonkeyPatch):
    watch_calls: list[str] = []
    monkeypatch.setattr(
        "trade_integrations.autonomous_agents.nautilus_watch.ensure_nautilus_watch_for_agent",
        lambda aid: watch_calls.append(aid),
    )
    monkeypatch.setattr(
        "nautilus_openalgo_bridge.handoff.sync_watch_spec_to_handoff",
        lambda *_a, **_k: None,
    )
    agent = _agent()
    agent["last_decision"] = {"decision": "HOLD", "at": "2026-07-16T20:00:00+00:00"}
    agent["thesis"] = {"recommended": {"legs": [{"symbol": "NIFTY", "side": "BUY", "qty": 1}]}}
    agent["watch_spec"] = {"rules": [{"symbol": "NIFTY", "metric": "spot_move_pct", "threshold": 1.0}]}
    save_agent(agent)
    assert finalize_bootstrap_if_ready("aa_boot") is True
    updated = get_agent("aa_boot")
    assert updated["bootstrap_status"] == "done"
    assert updated.get("plan_approved_at")
    assert not updated.get("plan_approval_required")
    assert updated.get("bootstrap_completed_at")
    assert watch_calls == ["aa_boot"]


def test_finalize_bootstrap_requires_watch_spec(hub_tmp: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(
        "trade_integrations.autonomous_agents.nautilus_watch.ensure_nautilus_watch_for_agent",
        lambda *_a, **_k: None,
    )
    agent = _agent()
    agent["last_decision"] = {"decision": "HOLD", "at": "2026-07-16T20:00:00+00:00"}
    agent["thesis"] = {"recommended": {"legs": [{"symbol": "NIFTY", "side": "BUY", "qty": 1}]}}
    save_agent(agent)
    assert finalize_bootstrap_if_ready("aa_boot") is False
    updated = get_agent("aa_boot")
    assert updated["bootstrap_status"] == "running"


def test_register_jobs_defers_research_while_bootstrap_pending(hub_tmp: Path):
    agent = _agent()
    agent["bootstrap_status"] = "pending"
    save_agent(agent)
    register_agent_jobs(agent)
    store = ScheduledResearchJobStore()
    job = store.get("aa_boot-research")
    assert job is not None
    now_ms = int(time.time() * 1000)
    research_ms = int(agent["schedules"]["research_ms"])
    assert job.next_run_at >= now_ms + research_ms - 5_000


def test_research_turn_recently_ran_within_cooldown():
    agent = {
        "last_full_reasoning_at": "2099-01-01T12:00:00+00:00",
        "last_revision_at": None,
    }
    assert _research_turn_recently_ran(agent) is True


def test_research_turn_not_recent_after_revision():
    agent = {
        "last_full_reasoning_at": "2099-01-01T12:00:00+00:00",
        "last_revision_at": "2099-01-01T12:05:00+00:00",
    }
    assert _research_turn_recently_ran(agent) is False
