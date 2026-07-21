"""Tests for stale pending bootstrap watchdog."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import patch


def test_resume_stale_pending_bootstraps_skips_infra_paused(tmp_path, monkeypatch) -> None:
    hub = tmp_path / "hub"
    (hub / "_data" / "autonomous_agents").mkdir(parents=True)
    monkeypatch.setattr("trade_integrations.context.hub.get_hub_dir", lambda: hub)

    old = (datetime.now(timezone.utc) - timedelta(seconds=120)).isoformat()
    agent = {
        "id": "aa_stale1",
        "status": "running",
        "bootstrap_status": "pending",
        "pause_reason": "infra",
        "created_at": old,
    }
    from trade_integrations.autonomous_agents.store import save_agent

    save_agent(agent)

    from src.scheduled_research.autonomous_bootstrap import resume_stale_pending_bootstraps

    with patch("src.scheduled_research.autonomous_bootstrap.schedule_agent_bootstrap", return_value=True) as sched:
        count = resume_stale_pending_bootstraps(max_age_s=60)
    assert count == 0
    sched.assert_not_called()


def test_resume_stale_pending_bootstraps_reschedules_old_pending(tmp_path, monkeypatch) -> None:
    hub = tmp_path / "hub"
    (hub / "_data" / "autonomous_agents").mkdir(parents=True)
    monkeypatch.setattr("trade_integrations.context.hub.get_hub_dir", lambda: hub)

    old = (datetime.now(timezone.utc) - timedelta(seconds=120)).isoformat()
    agent = {
        "id": "aa_stale2",
        "status": "running",
        "bootstrap_status": "pending",
        "created_at": old,
    }
    from trade_integrations.autonomous_agents.store import save_agent

    save_agent(agent)

    from src.scheduled_research.autonomous_bootstrap import resume_stale_pending_bootstraps

    with patch("src.scheduled_research.autonomous_bootstrap.schedule_agent_bootstrap", return_value=True) as sched:
        count = resume_stale_pending_bootstraps(max_age_s=60)
    assert count == 1
    sched.assert_called_once_with("aa_stale2")
