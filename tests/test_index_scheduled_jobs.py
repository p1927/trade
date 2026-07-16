"""Unit tests for scheduled index research job dispatch."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

AGENT_ROOT = Path(__file__).resolve().parents[1] / "vibetrading" / "agent"
if str(AGENT_ROOT) not in sys.path:
    sys.path.insert(0, str(AGENT_ROOT))

from src.scheduled_research.index_jobs import (
    JOB_TYPE_INDEX_FACTOR_SNAPSHOT,
    JOB_TYPE_INDEX_RESEARCH,
    INDEX_JOB_TYPES,
    dispatch_index_job_sync,
    is_index_scheduler_enabled,
    register_default_index_jobs,
)
from src.scheduled_research.models import JobStatus, ScheduledResearchJob
from src.scheduled_research.store import ScheduledResearchJobStore


@pytest.mark.unit
class TestIndexSchedulerEnv:
    def test_enabled_when_env_true(self):
        assert is_index_scheduler_enabled("true") is True
        assert is_index_scheduler_enabled("1") is True

    def test_disabled_by_default(self, monkeypatch):
        monkeypatch.delenv("INDEX_RESEARCH_ENABLE_SCHEDULER", raising=False)
        assert is_index_scheduler_enabled() is False


@pytest.mark.unit
class TestIndexJobDispatch:
    def test_snapshot_job_calls_run_snapshot(self):
        job = ScheduledResearchJob(
            id="snap-1",
            prompt="snapshot",
            schedule="86400000",
            config={"job_type": JOB_TYPE_INDEX_FACTOR_SNAPSHOT},
        )
        with patch(
            "src.scheduled_research.index_jobs.run_index_factor_snapshot_job",
            return_value={"rows": 3},
        ) as run_mock:
            dispatch_index_job_sync(job)
        run_mock.assert_called_once_with(job.config)

    def test_research_job_calls_pipeline(self):
        job = ScheduledResearchJob(
            id="research-1",
            prompt="research",
            schedule="604800000",
            config={"job_type": JOB_TYPE_INDEX_RESEARCH, "ticker": "NIFTY"},
        )
        with patch("src.scheduled_research.index_jobs.run_index_research_job") as run_mock:
            dispatch_index_job_sync(job)
        run_mock.assert_called_once_with(job.config)

    def test_unknown_job_type_raises(self):
        job = ScheduledResearchJob(
            id="bad",
            prompt="bad",
            schedule="60000",
            config={"job_type": "unknown"},
        )
        with pytest.raises(ValueError, match="unsupported index job_type"):
            dispatch_index_job_sync(job)


@pytest.mark.unit
class TestIndexJobRegistration:
    def test_registers_defaults_when_missing(self, tmp_path):
        store = ScheduledResearchJobStore(path=tmp_path / "jobs.json")
        created = register_default_index_jobs(store)
        assert created == 2
        jobs = store.load()
        assert "nifty-index-factor-snapshot" in jobs
        assert "nifty-index-research" in jobs
        assert jobs["nifty-index-factor-snapshot"].config["job_type"] == JOB_TYPE_INDEX_FACTOR_SNAPSHOT
        assert jobs["nifty-index-research"].config["job_type"] == JOB_TYPE_INDEX_RESEARCH

    def test_idempotent_registration(self, tmp_path):
        store = ScheduledResearchJobStore(path=tmp_path / "jobs.json")
        assert register_default_index_jobs(store) == 2
        assert register_default_index_jobs(store) == 0

    def test_index_job_types_frozen(self):
        assert JOB_TYPE_INDEX_FACTOR_SNAPSHOT in INDEX_JOB_TYPES
        assert JOB_TYPE_INDEX_RESEARCH in INDEX_JOB_TYPES


@pytest.mark.unit
class TestScheduledRoutesDispatchRouting:
    @pytest.mark.asyncio
    async def test_routes_index_job_types(self):
        from src.api import scheduled_routes

        job = ScheduledResearchJob(
            id="route-test",
            prompt="index",
            schedule="60000",
            config={"job_type": JOB_TYPE_INDEX_RESEARCH},
        )
        with patch("src.scheduled_research.index_jobs.dispatch_index_job") as dispatch_mock:
            dispatch_mock.return_value = None
            await scheduled_routes._dispatch_scheduled_research_job(job)
        dispatch_mock.assert_awaited_once_with(job)

    @pytest.mark.asyncio
    async def test_default_jobs_use_agent_session(self):
        from src.api import scheduled_routes

        job = ScheduledResearchJob(
            id="agent-test",
            prompt="analyze RELIANCE",
            schedule="60000",
            config={},
        )
        session = MagicMock(session_id="sess-1")
        svc = MagicMock()
        svc.create_session.return_value = session
        svc.send_message = AsyncMock()
        host = MagicMock()
        host._get_session_service.return_value = svc

        with patch.dict("sys.modules", {"api_server": host}):
            await scheduled_routes._dispatch_scheduled_research_job(job)

        svc.create_session.assert_called_once()
        svc.send_message.assert_awaited_once_with("sess-1", "analyze RELIANCE")
