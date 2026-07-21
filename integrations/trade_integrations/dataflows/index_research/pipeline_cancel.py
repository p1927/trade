"""Cooperative cancellation for long index-research pipeline runs.

Global flag: ``log/index_prediction_jobs/_pipeline_cancel.json`` (API shutdown).
Per-job flag: ``log/index_prediction_jobs/{job_id}/cancel.json`` (user cancel).
"""

from __future__ import annotations

import contextvars
import json
import os
import time
from pathlib import Path

_current_job_id: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "pipeline_job_id",
    default=None,
)


class PipelineCancelledError(Exception):
    """Raised when the pipeline should stop (server reload/shutdown/cancel)."""

    def __init__(self, reason: str = "server_reloading") -> None:
        self.reason = reason
        super().__init__(reason)


def _jobs_root() -> Path:
    env = os.getenv("TRADE_STACK_ROOT", "").strip()
    if env:
        root = Path(env).expanduser().resolve()
    else:
        root = None
        here = Path(__file__).resolve()
        for parent in here.parents:
            if (parent / "integrations" / "trade_integrations").is_dir():
                root = parent
                break
        if root is None:
            root = Path.cwd()
    return root / "log" / "index_prediction_jobs"


def set_pipeline_job_id(job_id: str | None) -> None:
    """Bind the current worker to a manual run job (for scoped cancel checks)."""
    _current_job_id.set(job_id)


def _global_cancel_path() -> Path:
    return _jobs_root() / "_pipeline_cancel.json"


def _job_cancel_path(job_id: str) -> Path:
    return _jobs_root() / job_id / "cancel.json"


def request_pipeline_cancel(reason: str = "server_reloading", *, job_id: str | None = None) -> None:
    root = _jobs_root()
    root.mkdir(parents=True, exist_ok=True)
    payload = {"reason": reason, "at": time.time(), "job_id": job_id}
    if job_id:
        path = _job_cancel_path(job_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload), encoding="utf-8")
    else:
        _global_cancel_path().write_text(json.dumps(payload), encoding="utf-8")


def clear_pipeline_cancel(*, job_id: str | None = None) -> None:
    try:
        if job_id:
            _job_cancel_path(job_id).unlink(missing_ok=True)
        else:
            _global_cancel_path().unlink(missing_ok=True)
    except OSError:
        pass


def _read_cancel_reason(path: Path) -> str | None:
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return str(payload.get("reason") or "cancelled")
    except (json.JSONDecodeError, OSError, TypeError):
        return "cancelled"


def check_pipeline_cancel() -> None:
    reason = _read_cancel_reason(_global_cancel_path())
    if reason:
        raise PipelineCancelledError(reason)
    job_id = _current_job_id.get()
    if job_id:
        reason = _read_cancel_reason(_job_cancel_path(job_id))
        if reason:
            raise PipelineCancelledError(reason)
