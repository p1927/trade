"""Tests for file-backed index prediction run jobs."""

from __future__ import annotations

import pytest


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
