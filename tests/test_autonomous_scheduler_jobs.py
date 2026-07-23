"""Scheduler registration preserves overdue watch jobs."""

from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
AGENT_SRC = ROOT / "vibetrading" / "agent"
if str(AGENT_SRC) not in sys.path:
    sys.path.insert(0, str(AGENT_SRC))

from src.scheduled_research.autonomous_agent_jobs import (  # noqa: E402
    _resolve_watch_job_timing,
    register_agent_jobs,
)
from src.scheduled_research.models import JobStatus, ScheduledResearchJob  # noqa: E402
from src.scheduled_research.store import ScheduledResearchJobStore  # noqa: E402


@pytest.fixture
def jobs_store(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    jobs_dir = tmp_path / "jobs"
    jobs_dir.mkdir()
    monkeypatch.setenv("SCHEDULED_RESEARCH_JOBS_DIR", str(jobs_dir))
    monkeypatch.setenv("AUTONOMOUS_AGENTS_ENABLE_SCHEDULER", "1")
    return ScheduledResearchJobStore()


def test_resolve_watch_job_timing_preserves_overdue_pending():
    now_ms = int(time.time() * 1000)
    overdue_at = now_ms - 30_000
    existing = ScheduledResearchJob(
        id="aa_x-watch",
        prompt="watch",
        schedule="60000",
        next_run_at=overdue_at,
        status=JobStatus.PENDING,
        created_at=now_ms - 120_000,
        config={"job_type": "autonomous_agent_watch"},
    )
    next_run, created_at, last_run = _resolve_watch_job_timing(
        existing, watch_ms=60_000, now_ms=now_ms
    )
    assert next_run == overdue_at
    assert created_at == existing.created_at
    assert last_run is None


def test_register_agent_jobs_preserves_overdue_watch(jobs_store: ScheduledResearchJobStore, monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    hub = tmp_path / "hub"
    hub.mkdir()
    monkeypatch.setenv("TRADE_STACK_HUB_DIR", str(hub))
    now_ms = int(time.time() * 1000)
    overdue_at = now_ms - 15_000
    jobs_store.upsert(
        ScheduledResearchJob(
            id="aa_reg-watch",
            prompt="watch",
            schedule="60000",
            next_run_at=overdue_at,
            status=JobStatus.PENDING,
            created_at=now_ms - 60_000,
            config={"job_type": "autonomous_agent_watch", "autonomous_agent_id": "aa_reg"},
        )
    )
    agent = {
        "id": "aa_reg",
        "status": "running",
        "bootstrap_status": "done",
        "schedules": {"watch_ms": 60_000, "research_ms": 3_600_000},
        "symbols": ["NIFTY"],
    }
    register_agent_jobs(agent)
    job = jobs_store.get("aa_reg-watch")
    assert job is not None
    assert job.next_run_at == overdue_at
