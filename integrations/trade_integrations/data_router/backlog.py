"""Background job backlog for tiered and mission fetches."""

from __future__ import annotations

import hashlib
import json
import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from trade_integrations.context.hub import get_hub_dir
from trade_integrations.data_router.types import FetchSpec

logger = logging.getLogger(__name__)

_BACKLOG_REL = Path("_data") / "backlog"
_PENDING = "pending.jsonl"
_COMPLETED = "completed.jsonl"
_FAILED = "failed.jsonl"
_HEARTBEAT = "worker_heartbeat.json"


def _backlog_dir() -> Path:
    path = get_hub_dir() / _BACKLOG_REL
    path.mkdir(parents=True, exist_ok=True)
    return path


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def job_id_for_spec(spec: FetchSpec, source_id: str) -> str:
    payload = f"{spec.spec_hash()}:{source_id}"
    return hashlib.sha256(payload.encode()).hexdigest()[:16]


def enqueue(spec: FetchSpec, source_id: str, *, priority: int = 0) -> tuple[str, bool]:
    """Append job; returns (job_id, appended). Dedupes pending queue."""
    job_id = job_id_for_spec(spec, source_id)
    pending_path = _backlog_dir() / _PENDING
    if pending_path.is_file():
        for line in pending_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if row.get("job_id") == job_id:
                return job_id, False
    payload = {
        "job_id": job_id,
        "spec": {
            "domain": spec.domain,
            "market": spec.market,
            "symbol": spec.symbol,
            "start": spec.start,
            "end": spec.end,
            "extra": spec.extra,
        },
        "source_id": source_id,
        "priority": priority,
        "enqueued_at": _now_iso(),
    }
    with pending_path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(payload, default=str) + "\n")
    return job_id, True


def dequeue_next() -> dict[str, Any] | None:
    pending_path = _backlog_dir() / _PENDING
    if not pending_path.is_file():
        return None
    lines = [ln for ln in pending_path.read_text(encoding="utf-8").splitlines() if ln.strip()]
    if not lines:
        return None
    rows = []
    for line in lines:
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    if not rows:
        return None
    rows.sort(key=lambda r: (-int(r.get("priority", 0)), str(r.get("enqueued_at", ""))))
    job = rows[0]
    remaining = [json.dumps(r, default=str) for r in rows if r.get("job_id") != job.get("job_id")]
    pending_path.write_text("\n".join(remaining) + ("\n" if remaining else ""), encoding="utf-8")
    return job


def spec_from_job(job: dict[str, Any]) -> FetchSpec:
    s = job.get("spec") or {}
    return FetchSpec(
        domain=str(s.get("domain") or ""),
        market=str(s.get("market") or ""),
        symbol=s.get("symbol"),
        start=s.get("start"),
        end=s.get("end"),
        extra=dict(s.get("extra") or {}),
    )


def mark_completed(job: dict[str, Any]) -> None:
    row = {**job, "completed_at": _now_iso()}
    path = _backlog_dir() / _COMPLETED
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, default=str) + "\n")


def mark_failed(job: dict[str, Any], error: str) -> None:
    row = {**job, "failed_at": _now_iso(), "error": error[:500]}
    path = _backlog_dir() / _FAILED
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, default=str) + "\n")


def requeue(job: dict[str, Any]) -> None:
    job_id = job.get("job_id")
    pending_path = _backlog_dir() / _PENDING
    if job_id and pending_path.is_file():
        for line in pending_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if row.get("job_id") == job_id:
                return
    with pending_path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(job, default=str) + "\n")


def pending_count() -> int:
    pending_path = _backlog_dir() / _PENDING
    if not pending_path.is_file():
        return 0
    return sum(1 for ln in pending_path.read_text(encoding="utf-8").splitlines() if ln.strip())


def write_heartbeat(*, jobs_processed: int = 0) -> None:
    path = _backlog_dir() / _HEARTBEAT
    path.write_text(
        json.dumps({"at": _now_iso(), "jobs_processed": jobs_processed}, indent=2),
        encoding="utf-8",
    )


def read_heartbeat() -> dict[str, Any]:
    path = _backlog_dir() / _HEARTBEAT
    if not path.is_file():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def wait_for_job(job_id: str, *, timeout_sec: float = 30.0, poll_sec: float = 0.5) -> bool:
    """Poll normalized store / completed log until job finishes or timeout."""
    import time

    deadline = time.monotonic() + timeout_sec
    completed_path = _backlog_dir() / _COMPLETED
    while time.monotonic() < deadline:
        if completed_path.is_file():
            for line in completed_path.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if row.get("job_id") == job_id:
                    return True
        time.sleep(poll_sec)
    return False
