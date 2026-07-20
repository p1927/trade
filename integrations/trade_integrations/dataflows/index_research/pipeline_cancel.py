"""Cooperative cancellation for long index-research pipeline runs.

Uses a file flag under ``log/index_prediction_jobs/`` so subprocess workers
and in-process threads both observe API shutdown / dev reload.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path


class PipelineCancelledError(Exception):
    """Raised when the pipeline should stop (server reload/shutdown)."""

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


def _cancel_path() -> Path:
    return _jobs_root() / "_pipeline_cancel.json"


def request_pipeline_cancel(reason: str = "server_reloading") -> None:
    root = _jobs_root()
    root.mkdir(parents=True, exist_ok=True)
    payload = {"reason": reason, "at": time.time()}
    _cancel_path().write_text(json.dumps(payload), encoding="utf-8")


def clear_pipeline_cancel() -> None:
    try:
        _cancel_path().unlink(missing_ok=True)
    except OSError:
        pass


def check_pipeline_cancel() -> None:
    path = _cancel_path()
    if not path.is_file():
        return
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        reason = str(payload.get("reason") or "server_reloading")
    except (json.JSONDecodeError, OSError, TypeError):
        reason = "server_reloading"
    raise PipelineCancelledError(reason)
