"""Tests for file-backed index prediction run jobs."""

from __future__ import annotations

import os
import sys
import threading
from pathlib import Path

import pytest

AGENT_ROOT = Path(__file__).resolve().parents[1] / "vibetrading" / "agent"
if str(AGENT_ROOT) not in sys.path:
    sys.path.insert(0, str(AGENT_ROOT))


@pytest.mark.unit
def test_index_prediction_job_persists_to_disk(tmp_path, monkeypatch):
    from src.trade import index_prediction_run_jobs as jobs

    monkeypatch.setattr(jobs, "_jobs_root", lambda: tmp_path / "jobs")

    job_id, reused = jobs.start_job(
        ticker="NIFTY",
        horizon_days=14,
        refresh_constituents=False,
        run_forecast_lab=True,
    )
    assert reused is False
    assert jobs.job_id_valid(job_id)

    jobs.append_log(job_id, {"stage": "start", "message": "hello", "level": "info"})
    jobs.INDEX_PREDICTION_RUN_JOBS.clear()
    jobs._ACTIVE_BY_TICKER.clear()

    loaded = jobs.get_job(job_id)
    assert loaded is not None
    assert loaded["status"] in {"queued", "running"}
    assert loaded["logs"][-1]["message"] == "hello"

    jobs.complete_job(job_id, ticker="NIFTY", artifact={"ticker": "NIFTY", "view": "neutral"})
    jobs.INDEX_PREDICTION_RUN_JOBS.clear()
    done = jobs.get_job(job_id)
    assert done is not None
    assert done["status"] == "done"
    assert done["artifact"]["ticker"] == "NIFTY"


@pytest.mark.unit
def test_reconcile_zombie_job_when_worker_dead(tmp_path, monkeypatch):
    from src.trade import index_prediction_run_jobs as jobs

    monkeypatch.setattr(jobs, "_jobs_root", lambda: tmp_path / "jobs")

    job_id, _ = jobs.start_job(
        ticker="NIFTY",
        horizon_days=14,
        refresh_constituents=False,
        run_forecast_lab=False,
    )
    job = jobs._get_job_record(job_id)
    assert job is not None
    job["status"] = "running"
    job["worker_pid"] = 999999999
    jobs._write_job_to_disk(job)

    assert jobs.reconcile_zombie_job(job_id) is True
    done = jobs.get_job(job_id)
    assert done is not None
    assert done["status"] == "error"


@pytest.mark.unit
def test_pipeline_cancel_file_flag(tmp_path, monkeypatch):
    from trade_integrations.dataflows.index_research import pipeline_cancel as pc

    root = tmp_path / "jobs"
    monkeypatch.setattr(pc, "_jobs_root", lambda: root)

    pc.clear_pipeline_cancel()
    pc.check_pipeline_cancel()

    pc.request_pipeline_cancel("server_shutting_down")
    with pytest.raises(pc.PipelineCancelledError) as exc:
        pc.check_pipeline_cancel()
    assert exc.value.reason == "server_shutting_down"

    pc.clear_pipeline_cancel()
    pc.check_pipeline_cancel()


@pytest.mark.unit
def test_reconcile_queued_job_no_pid(tmp_path, monkeypatch):
    from src.trade import index_prediction_run_jobs as jobs

    monkeypatch.setattr(jobs, "_jobs_root", lambda: tmp_path / "jobs")
    monkeypatch.setattr(jobs, "_QUEUED_NO_PID_SECONDS", 5)

    job_id, _ = jobs.start_job(
        ticker="NIFTY",
        horizon_days=14,
        refresh_constituents=False,
        run_forecast_lab=False,
    )
    job = jobs._get_job_record(job_id)
    assert job is not None
    job["created_at"] = "2020-01-01T00:00:00+00:00"
    jobs._write_job_to_disk(job)

    assert jobs.reconcile_queued_job(job_id) is True
    done = jobs.get_job(job_id)
    assert done is not None
    assert done["status"] == "error"
    assert "never spawned" in (done.get("error") or "")


@pytest.mark.unit
def test_reconcile_stale_job_wall_clock_exceeded(tmp_path, monkeypatch):
    from src.trade import index_prediction_run_jobs as jobs

    monkeypatch.setattr(jobs, "_jobs_root", lambda: tmp_path / "jobs")
    monkeypatch.setattr(jobs, "_WALL_CLOCK_SECONDS", 120)

    job_id, _ = jobs.start_job(
        ticker="NIFTY",
        horizon_days=14,
        refresh_constituents=False,
        run_forecast_lab=False,
    )
    job = jobs._get_job_record(job_id)
    assert job is not None
    job["status"] = "running"
    job["worker_pid"] = os.getpid()
    job["created_at"] = "2020-01-01T00:00:00+00:00"
    job["logs"] = [{"stage": "predict", "message": "running", "at": "2026-07-21T12:00:00+00:00"}]
    jobs._write_job_to_disk(job)

    assert jobs.reconcile_stale_job(job_id) is True
    done = jobs.get_job(job_id)
    assert done is not None
    assert done["status"] == "error"
    assert "wall-clock" in (done.get("error") or "")


@pytest.mark.unit
def test_complete_job_skips_when_already_terminal(tmp_path, monkeypatch):
    from src.trade import index_prediction_run_jobs as jobs

    monkeypatch.setattr(jobs, "_jobs_root", lambda: tmp_path / "jobs")
    job_id, _ = jobs.start_job(
        ticker="NIFTY",
        horizon_days=14,
        refresh_constituents=False,
        run_forecast_lab=False,
    )
    jobs.fail_job(job_id, "reconciled stale")
    jobs.complete_job(job_id, ticker="NIFTY", artifact={"ticker": "NIFTY", "view": "bullish"})
    done = jobs.get_job(job_id)
    assert done is not None
    assert done["status"] == "error"
    assert done.get("artifact") is None


@pytest.mark.unit
def test_job_snapshot_includes_progress_fields(tmp_path, monkeypatch):
    from src.trade import index_prediction_run_jobs as jobs

    monkeypatch.setattr(jobs, "_jobs_root", lambda: tmp_path / "jobs")
    job_id, _ = jobs.start_job(
        ticker="NIFTY",
        horizon_days=14,
        refresh_constituents=False,
        run_forecast_lab=False,
    )
    jobs.append_log(
        job_id,
        {
            "stage": "forecast_lab",
            "message": "Track xgboost_macro",
            "level": "info",
            "at": "2026-07-21T12:00:00+00:00",
            "detail": {"elapsed_ms": 1200.0, "track_id": "xgboost_macro"},
        },
    )
    snap = jobs.get_job(job_id)
    assert snap is not None
    assert snap["current_stage"] == "forecast_lab"
    assert snap["stage_elapsed_ms"] == 1200.0
    assert snap["current_track_id"] == "xgboost_macro"


@pytest.mark.unit
def test_pipeline_cancel_scoped_to_job(tmp_path, monkeypatch):
    from trade_integrations.dataflows.index_research import pipeline_cancel as pc

    root = tmp_path / "jobs"
    monkeypatch.setattr(pc, "_jobs_root", lambda: root)

    pc.clear_pipeline_cancel()
    pc.clear_pipeline_cancel(job_id="job123")
    pc.request_pipeline_cancel("user_cancel:job123", job_id="job123")
    pc.set_pipeline_job_id("job123")
    with pytest.raises(pc.PipelineCancelledError) as exc:
        pc.check_pipeline_cancel()
    assert "user_cancel" in exc.value.reason
    pc.set_pipeline_job_id("other")
    pc.check_pipeline_cancel()
    pc.set_pipeline_job_id(None)
    pc.clear_pipeline_cancel(job_id="job123")


@pytest.mark.unit
def test_concurrent_append_log_preserves_all_entries(tmp_path, monkeypatch):
    """Cross-process-safe append_log must not lose entries under parallel writers."""
    from src.trade import index_prediction_run_jobs as jobs

    monkeypatch.setattr(jobs, "_jobs_root", lambda: tmp_path / "jobs")
    jobs.INDEX_PREDICTION_RUN_JOBS.clear()
    jobs._ACTIVE_BY_TICKER.clear()

    job_id, _ = jobs.start_job(
        ticker="NIFTY",
        horizon_days=14,
        refresh_constituents=False,
        run_forecast_lab=False,
    )
    jobs.INDEX_PREDICTION_RUN_JOBS.clear()
    jobs._ACTIVE_BY_TICKER.clear()

    per_thread = 25
    thread_count = 4

    def writer(thread_idx: int) -> None:
        for i in range(per_thread):
            jobs.append_log(
                job_id,
                {
                    "stage": "test",
                    "message": f"t{thread_idx}-e{i}",
                    "level": "info",
                    "at": f"2026-07-21T12:{thread_idx:02d}:{i:02d}+00:00",
                },
            )

    threads = [threading.Thread(target=writer, args=(t,)) for t in range(thread_count)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    jobs.INDEX_PREDICTION_RUN_JOBS.clear()
    loaded = jobs.get_job(job_id)
    assert loaded is not None
    assert len(loaded["logs"]) == per_thread * thread_count
    messages = {entry["message"] for entry in loaded["logs"]}
    assert len(messages) == per_thread * thread_count
