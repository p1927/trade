"""Tests for autonomous agent recovery (streaming + bootstrap finalize)."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from unittest.mock import patch


def test_recover_stale_agent_streaming_clears_orphan_flag(tmp_path, monkeypatch) -> None:
    hub = tmp_path / "hub"
    (hub / "_data" / "autonomous_agents").mkdir(parents=True)
    monkeypatch.setattr("trade_integrations.context.hub.get_hub_dir", lambda: hub)

    old = (datetime.now(timezone.utc) - timedelta(seconds=300)).isoformat()
    agent = {
        "id": "aa_stream1",
        "status": "running",
        "bootstrap_status": "running",
        "streaming": True,
        "last_full_reasoning_at": old,
        "updated_at": old,
        "vibe_session_id": "sess_stream1",
    }
    from trade_integrations.autonomous_agents.store import save_agent

    save_agent(agent)

    from trade_integrations.autonomous_agents.recovery import recover_stale_agent_streaming

    with patch(
        "trade_integrations.autonomous_agents.recovery.is_session_turn_in_flight",
        return_value=False,
    ):
        count = recover_stale_agent_streaming(max_age_s=120)

    assert count == 1
    from trade_integrations.autonomous_agents.store import get_agent

    latest = get_agent("aa_stream1")
    assert latest is not None
    assert latest.get("streaming") is False


def test_recover_stale_agent_streaming_skips_live_session(tmp_path, monkeypatch) -> None:
    hub = tmp_path / "hub"
    (hub / "_data" / "autonomous_agents").mkdir(parents=True)
    monkeypatch.setattr("trade_integrations.context.hub.get_hub_dir", lambda: hub)

    old = (datetime.now(timezone.utc) - timedelta(seconds=300)).isoformat()
    agent = {
        "id": "aa_stream2",
        "status": "running",
        "streaming": True,
        "last_full_reasoning_at": old,
        "vibe_session_id": "sess_live",
    }
    from trade_integrations.autonomous_agents.store import save_agent

    save_agent(agent)

    from trade_integrations.autonomous_agents.recovery import recover_stale_agent_streaming

    with patch(
        "trade_integrations.autonomous_agents.recovery.is_session_turn_in_flight",
        return_value=True,
    ):
        count = recover_stale_agent_streaming(max_age_s=120)

    assert count == 0


def test_is_session_turn_in_flight_detects_recent_running_attempt(tmp_path, monkeypatch) -> None:
    sessions = tmp_path / "sessions"
    sid = "sess_attempt1"
    attempt_dir = sessions / sid / "attempts" / "att1"
    attempt_dir.mkdir(parents=True)
    recent = datetime.now(timezone.utc).isoformat()
    attempt_dir.joinpath("attempt.json").write_text(
        json.dumps({"status": "running", "created_at": recent}),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "trade_integrations.autonomous_agents.recovery._vibe_sessions_dir",
        lambda: sessions,
    )

    from trade_integrations.autonomous_agents.recovery import is_session_turn_in_flight

    assert is_session_turn_in_flight(sid) is True


def test_recover_bootstrap_finalize_blocked_schedules_recovery(tmp_path, monkeypatch) -> None:
    hub = tmp_path / "hub"
    agents_dir = hub / "_data" / "autonomous_agents"
    agents_dir.mkdir(parents=True)
    monkeypatch.setattr("trade_integrations.context.hub.get_hub_dir", lambda: hub)

    old = (datetime.now(timezone.utc) - timedelta(seconds=600)).isoformat()
    agent = {
        "id": "aa_finalize1",
        "status": "running",
        "bootstrap_status": "running",
        "symbols": ["NIFTY"],
        "constraints": {"instruments": ["options"]},
        "last_decision": {"decision": "HOLD"},
        "updated_at": old,
        "vibe_session_id": "sess_finalize1",
    }
    agents_dir.joinpath("aa_finalize1.json").write_text(json.dumps(agent), encoding="utf-8")

    from trade_integrations.autonomous_agents.recovery import recover_bootstrap_finalize_blocked

    with patch(
        "trade_integrations.autonomous_agents.recovery._schedule_bootstrap_structure_recovery",
        return_value=True,
    ) as sched:
        count = recover_bootstrap_finalize_blocked(max_age_s=300)

    assert count == 1
    sched.assert_called_once()
