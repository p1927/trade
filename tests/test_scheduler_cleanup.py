"""Tests for autonomous agent scheduler job cleanup."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

AGENT_ROOT = Path(__file__).resolve().parents[1] / "vibetrading" / "agent"
if str(AGENT_ROOT) not in sys.path:
    sys.path.insert(0, str(AGENT_ROOT))

from trade_integrations.autonomous_agents.scheduler_cleanup import (
    OBSOLETE_SCHEDULER_JOB_IDS,
    _delete_job_ids_from_store,
    agent_scheduler_job_ids,
    remove_agent_scheduler_jobs,
    remove_obsolete_scheduler_jobs,
)
from src.scheduled_research.store import ScheduledResearchJobStore


def test_obsolete_scheduler_job_ids() -> None:
    assert OBSOLETE_SCHEDULER_JOB_IDS == frozenset(
        {
            "auto-paper-agent-turn",
            "auto-paper-intraday",
            "auto-paper-thesis-break",
            "auto-paper-scheduler-health",
            "auto-paper-session-close-flatten",
        }
    )


def test_agent_scheduler_job_ids() -> None:
    assert agent_scheduler_job_ids("aa_test") == frozenset(
        {"aa_test-watch", "aa_test-research", "aa_test-quant", "aa_test-infra-heal"}
    )


def test_delete_job_ids_json_fallback(tmp_path: Path) -> None:
    store_path = tmp_path / "scheduled_research" / "scheduled_research_jobs.json"
    store_path.parent.mkdir(parents=True)
    store_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "jobs": [
                    {"id": "aa_one-watch", "status": "pending"},
                    {"id": "keep-me", "status": "pending"},
                ],
            }
        ),
        encoding="utf-8",
    )
    removed = _delete_job_ids_from_store(frozenset({"aa_one-watch", "missing-id"}), store_path=store_path)
    assert removed["aa_one-watch"] is True
    assert removed["missing-id"] is False
    payload = json.loads(store_path.read_text(encoding="utf-8"))
    assert [j["id"] for j in payload["jobs"]] == ["keep-me"]


def test_delete_job_ids_partial_store_success_falls_back_to_json(tmp_path: Path, monkeypatch) -> None:
    store_path = tmp_path / "scheduled_research" / "scheduled_research_jobs.json"
    store_path.parent.mkdir(parents=True)
    store_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "jobs": [
                    {"id": "aa_two-watch", "status": "pending"},
                    {"id": "aa_two-research", "status": "pending"},
                ],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "trade_integrations.autonomous_agents.scheduler_cleanup._default_store_path",
        lambda: store_path,
    )

    class FakeStore:
        def delete(self, job_id: str) -> bool:
            if job_id != "aa_two-watch":
                return False
            payload = json.loads(store_path.read_text(encoding="utf-8"))
            jobs = [j for j in payload.get("jobs", []) if j.get("id") != job_id]
            payload["jobs"] = jobs
            store_path.write_text(json.dumps(payload), encoding="utf-8")
            return True

    monkeypatch.setattr(
        "src.scheduled_research.store.ScheduledResearchJobStore",
        lambda *a, **k: FakeStore(),
    )

    removed = _delete_job_ids_from_store(frozenset({"aa_two-watch", "aa_two-research"}))
    assert removed["aa_two-watch"] is True
    assert removed["aa_two-research"] is True
    payload = json.loads(store_path.read_text(encoding="utf-8"))
    assert payload["jobs"] == []


def test_remove_agent_and_obsolete_jobs(tmp_path: Path) -> None:
    store_path = tmp_path / "scheduled_research" / "scheduled_research_jobs.json"
    store_path.parent.mkdir(parents=True)
    store_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "jobs": [
                    {"id": "aa_rm-watch", "status": "pending"},
                    {"id": "auto-paper-agent-turn", "status": "pending"},
                ],
            }
        ),
        encoding="utf-8",
    )
    agent_removed = remove_agent_scheduler_jobs("aa_rm", store_path=store_path)
    assert agent_removed["aa_rm-watch"] is True
    obsolete_removed = remove_obsolete_scheduler_jobs(store_path=store_path)
    assert obsolete_removed["auto-paper-agent-turn"] is True
    assert json.loads(store_path.read_text(encoding="utf-8"))["jobs"] == []


def test_scheduled_research_store_path_uses_runtime_root_env(tmp_path: Path, monkeypatch) -> None:
    runtime = tmp_path / "custom-runtime"
    runtime.mkdir()
    monkeypatch.setenv("VIBE_TRADING_RUNTIME_ROOT", str(runtime))
    store = ScheduledResearchJobStore()
    assert store.path == runtime / "scheduled_research" / "scheduled_research_jobs.json"
