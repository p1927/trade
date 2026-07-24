"""Tests for Tier 0 trade observability."""

from __future__ import annotations

import json

import pytest

from trade_integrations.observability.emitter import emit, emit_job_rollup, is_observability_enabled
from trade_integrations.observability.issues import list_issues, open_issue_count, resolve_issue
from trade_integrations.observability.loop_guard import LoopGuard, LoopLimitReached
from trade_integrations.observability.paths import events_path, issues_path
from trade_integrations.observability.rollup import JobRollup
from trade_integrations.observability.store import read_jsonl_tail


@pytest.fixture(autouse=True)
def reset_issue_state():
    import trade_integrations.observability.issues as issues_mod

    issues_mod._open_cache.clear()
    issues_mod._skip_window.clear()
    issues_mod._ingest_fail_window.clear()
    yield
    issues_mod._open_cache.clear()
    issues_mod._skip_window.clear()
    issues_mod._ingest_fail_window.clear()


@pytest.fixture
def obs_paths(tmp_path, monkeypatch):
    events = tmp_path / "events.jsonl"
    issues = tmp_path / "issues.jsonl"
    monkeypatch.setenv("TRADE_OBSERVABILITY_EVENTS_PATH", str(events))
    monkeypatch.setenv("TRADE_OBSERVABILITY_ISSUES_PATH", str(issues))
    monkeypatch.setenv("TRADE_OBSERVABILITY_ENABLED", "1")
    return events, issues


def test_emit_writes_jsonl(obs_paths):
    events_file, _ = obs_paths
    emit("system", "test_event", level="info", detail={"ok": True})
    rows = read_jsonl_tail(events_file, limit=10)
    assert len(rows) == 1
    assert rows[0]["module"] == "system"
    assert rows[0]["event"] == "test_event"
    assert rows[0]["detail"]["ok"] is True


def test_error_event_opens_issue(obs_paths):
    _, issues_file = obs_paths
    emit("ingest", "source_fetch_failed", level="error", detail={"source": "searxng", "error": "timeout"})
    open_rows = list_issues(status="open")
    assert len(open_rows) == 1
    assert open_rows[0]["severity"] == "error"
    assert open_rows[0]["module"] == "ingest"
    assert issues_file.is_file()


def test_job_rollup_had_errors_opens_issue(obs_paths):
    emit_job_rollup(
        JobRollup(
            status="ok",
            had_errors=True,
            had_work=True,
            job_type="index_research",
            job_id="job-123",
        )
    )
    open_rows = list_issues(status="open", module="schedule")
    assert any(row.get("event") == "job_had_errors" for row in open_rows)


def test_silent_job_failure_when_no_work(obs_paths):
    emit_job_rollup(
        JobRollup(
            status="ok",
            had_errors=False,
            had_work=False,
            expected_work=True,
            job_type="news_entity",
            job_id="job-456",
        )
    )
    open_rows = list_issues(status="open")
    assert any(row.get("event") == "silent_job_failure" for row in open_rows)


def test_resolve_issue(obs_paths):
    emit("system", "boom", level="error", detail={"error_class": "RuntimeError"})
    open_rows = list_issues(status="open")
    issue_id = open_rows[0]["issue_id"]
    assert resolve_issue(issue_id) is True
    assert open_issue_count() == 0


def test_loop_guard_emits_limit(obs_paths):
    events_file, _ = obs_paths
    guard = LoopGuard("test_loop", module="system", max_iterations=2, strict=False)
    assert guard.tick() is True
    assert guard.tick() is False
    rows = read_jsonl_tail(events_file, limit=20)
    assert any(r.get("event") == "loop_limit_reached" for r in rows)


def test_loop_guard_strict_raises(obs_paths):
    guard = LoopGuard("strict_loop", max_iterations=1, strict=True)
    with pytest.raises(LoopLimitReached):
        guard.tick()


def test_observability_disabled(monkeypatch, obs_paths):
    monkeypatch.setenv("TRADE_OBSERVABILITY_ENABLED", "0")
    events_file, _ = obs_paths
    assert is_observability_enabled() is False
    emit("system", "ignored")
    assert read_jsonl_tail(events_file, limit=5) == []


def test_skip_reason_storm_creates_warn_issue(obs_paths, monkeypatch):
    monkeypatch.setattr(
        "trade_integrations.observability.issues.SKIP_REPEAT_THRESHOLD",
        3,
    )
    for _ in range(3):
        emit(
            "watch",
            "vibe_dispatch_skipped",
            level="warn",
            agent_id="aa_test",
            skip_reason="turn_in_flight",
        )
    open_rows = list_issues(status="open", module="watch")
    assert any(row.get("event") == "skip_reason_storm" for row in open_rows)
