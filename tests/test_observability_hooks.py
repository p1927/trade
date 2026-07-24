"""Tests for observability hooks."""

from __future__ import annotations

import pytest

from trade_integrations.observability.hooks import (
    emit_autonomous_decision,
    emit_autonomous_watch_tick,
    emit_ingest_complete,
    emit_pipeline_job_done,
)
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


def test_emit_pipeline_job_done(obs_paths):
    events_file, _ = obs_paths
    emit_pipeline_job_done(
        job_type="index_prediction",
        job_id="job-1",
        ticker="NIFTY",
        status="ok",
        had_errors=False,
    )
    rows = read_jsonl_tail(events_file, limit=10)
    assert any(r.get("event") == "index_prediction_job_done" for r in rows)


def test_emit_autonomous_watch_tick_error(obs_paths):
    events_file, _ = obs_paths
    emit_autonomous_watch_tick(
        "aa_test",
        {"status": "error", "reason": "nautilus_bridge_failed", "watch_path": "nautilus_scheduler_poll"},
    )
    rows = read_jsonl_tail(events_file, limit=10)
    assert any(r.get("event") == "autonomous_watch_tick" and r.get("level") == "error" for r in rows)


def test_emit_autonomous_decision(obs_paths):
    events_file, _ = obs_paths
    emit_autonomous_decision("aa_test", {"decision": "ENTER", "confidence": 82, "ticker": "NIFTY"})
    rows = read_jsonl_tail(events_file, limit=10)
    assert any(r.get("event") == "autonomous_decision" for r in rows)


def test_emit_ingest_complete_with_source_error(obs_paths):
    events_file, issues_file = obs_paths
    emit_ingest_complete(
        ticker="NIFTY",
        mode="full",
        sources={"searxng": {"error": "timeout"}},
        totals={"error": 1, "ingested": 0},
    )
    rows = read_jsonl_tail(events_file, limit=20)
    assert any(r.get("event") == "hub_news_ingest_complete" for r in rows)
    assert any(r.get("event") == "source_fetch_failed" for r in rows)
