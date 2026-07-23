"""Runtime status consistency for observe agents and scheduler poll paths."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from trade_integrations.autonomous_agents.runtime_status import (
    _nautilus_state_for_agent,
    _resolve_watch_path_for_agent,
    build_agent_runtime,
)


@pytest.mark.unit
def test_poll_ok_watch_path_when_nautilus_node_down(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(
        "trade_integrations.autonomous_agents.runtime_status._nautilus_watch_enabled",
        lambda: True,
    )
    monkeypatch.setattr(
        "trade_integrations.autonomous_agents.runtime_status._nautilus_process_alive",
        lambda: False,
    )

    fresh = (datetime.now(timezone.utc) - timedelta(minutes=2)).isoformat()
    agent = {
        "id": "aa_obs1",
        "symbols": ["NIFTY"],
        "status": "running",
        "bootstrap_status": "done",
        "plan_approved_at": fresh,
        "last_watch_at": fresh,
        "schedules": {"watch_ms": 420_000},
        "mandate_config": {"agent_mode": "observe"},
    }

    nautilus_state = _nautilus_state_for_agent(agent)
    assert nautilus_state == "poll_ok"

    path = _resolve_watch_path_for_agent(
        agent_id="aa_obs1",
        profile=type("P", (), {"uses_nautilus_watch": True})(),
        nautilus_on=True,
        nautilus_alive=False,
        in_registry=False,
        nautilus_bound_agent=None,
        nautilus_state=nautilus_state,
    )
    assert path == "nautilus_scheduler_poll"


@pytest.mark.unit
def test_awaiting_plan_approval_nautilus_expected(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(
        "trade_integrations.autonomous_agents.runtime_status._nautilus_watch_enabled",
        lambda: True,
    )
    monkeypatch.setattr(
        "trade_integrations.autonomous_agents.runtime_status._nautilus_process_alive",
        lambda: False,
    )

    agent = {
        "id": "aa_trade1",
        "symbols": ["NIFTY"],
        "status": "running",
        "bootstrap_status": "awaiting_plan_approval",
        "schedules": {"watch_ms": 420_000},
    }
    assert _nautilus_state_for_agent(agent) == "expected"


@pytest.mark.unit
def test_observe_agent_runtime_includes_watch_path(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(
        "trade_integrations.autonomous_agents.runtime_status._nautilus_watch_enabled",
        lambda: True,
    )
    monkeypatch.setattr(
        "trade_integrations.autonomous_agents.runtime_status._nautilus_process_alive",
        lambda: False,
    )
    monkeypatch.setattr(
        "trade_integrations.autonomous_agents.nautilus_watch.get_watch_process_status",
        lambda: {"bound_agent_id": None, "alive": False, "enabled": True},
    )

    fresh = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
    agent = {
        "id": "aa_obs2",
        "symbols": ["NIFTY"],
        "status": "running",
        "execution_market": "IN",
        "bootstrap_status": "done",
        "plan_approved_at": fresh,
        "last_watch_at": fresh,
        "schedules": {"watch_ms": 420_000},
        "mandate_config": {"agent_mode": "observe", "allowed_instruments": ["equity"]},
        "constraints": {"mode": "paper"},
    }
    runtime = build_agent_runtime(agent)
    assert runtime["nautilus_state"] == "poll_ok"
    assert runtime["watch_path"] == "nautilus_scheduler_poll"
