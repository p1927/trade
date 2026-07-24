"""Agent issue registry: fingerprints, dedupe, repeat-skip detection."""

from __future__ import annotations

import hashlib
import json
import threading
import time
from collections import defaultdict, deque
from dataclasses import replace
from typing import Any

from trade_integrations.observability.paths import issues_path
from trade_integrations.observability.schema import ObservabilityEvent, ObservabilityIssue
from trade_integrations.observability.store import append_jsonl, read_jsonl_tail

_lock = threading.Lock()
_open_cache: dict[str, ObservabilityIssue] = {}
_skip_window: dict[str, deque[float]] = defaultdict(deque)
_ingest_fail_window: dict[str, deque[float]] = defaultdict(deque)

SKIP_REPEAT_THRESHOLD = 10
SKIP_WINDOW_SECONDS = 300.0
INGEST_FAIL_THRESHOLD = 5
INGEST_WINDOW_SECONDS = 600.0


def _fingerprint(module: str, event: str, detail: dict[str, Any]) -> str:
    parts = [
        module,
        event,
        str(detail.get("error_class") or detail.get("skip_reason") or detail.get("source") or ""),
        str(detail.get("agent_id") or detail.get("job_id") or detail.get("job_type") or ""),
        str(detail.get("loop_name") or ""),
    ]
    raw = ":".join(p for p in parts if p)
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:12]
    return f"{module}:{event}:{digest}"


def _suggested_action(module: str, event: str, detail: dict[str, Any]) -> str:
    if event == "loop_limit_reached":
        return f"Inspect loop {detail.get('loop_name')} in module {module}; check termination condition."
    skip = detail.get("skip_reason")
    if skip:
        return f"Investigate repeated vibe skip_reason={skip} for agent {detail.get('agent_id', 'unknown')}."
    if detail.get("source"):
        return f"Check ingest source {detail['source']} health and backpressure env flags."
    if detail.get("had_errors"):
        return "Job reported had_errors=true; inspect child step failures in events.jsonl via job_id."
    return f"Review recent {module}/{event} events in log/observability/events.jsonl."


def _summary(module: str, event: str, detail: dict[str, Any]) -> str:
    if detail.get("message"):
        return str(detail["message"])[:220]
    if detail.get("skip_reason"):
        return f"{module}: repeated skip_reason={detail['skip_reason']}"
    if detail.get("source"):
        return f"{module}: source {detail['source']} failing"
    if detail.get("job_type"):
        return f"{module}: job {detail.get('job_id', '?')} ({detail['job_type']}) had_errors"
    return f"{module}: {event}"


def _append_issue_record(issue: ObservabilityIssue) -> None:
    append_jsonl(issues_path(), issue.to_dict())


def _load_open_from_disk() -> None:
    if _open_cache:
        return
    latest: dict[str, dict[str, Any]] = {}
    for row in read_jsonl_tail(issues_path(), limit=500):
        issue_id = str(row.get("issue_id") or "")
        if issue_id:
            latest[issue_id] = row
    for issue_id, row in latest.items():
        if row.get("status") != "open":
            continue
        _open_cache[issue_id] = ObservabilityIssue(
            issue_id=issue_id,
            severity=row.get("severity") or "error",
            module=row.get("module") or "system",
            event=row.get("event") or "unknown",
            status="open",
            first_seen=str(row.get("first_seen") or ""),
            last_seen=str(row.get("last_seen") or ""),
            count=int(row.get("count") or 1),
            summary=str(row.get("summary") or ""),
            detail=row.get("detail") if isinstance(row.get("detail"), dict) else {},
            suggested_action=str(row.get("suggested_action") or ""),
        )


def record_issue_from_event(event: ObservabilityEvent) -> ObservabilityIssue | None:
    """Open or bump an issue from an error-level event."""
    if event.level != "error":
        return None
    issue_id = _fingerprint(event.module, event.event, event.detail)
    with _lock:
        _load_open_from_disk()
        existing = _open_cache.get(issue_id)
        if existing:
            updated = replace(
                existing,
                last_seen=event.ts,
                count=existing.count + 1,
                detail={**existing.detail, **event.detail},
            )
            _open_cache[issue_id] = updated
            _append_issue_record(updated)
            return updated
        issue = ObservabilityIssue(
            issue_id=issue_id,
            severity="error",
            module=event.module,
            event=event.event,
            first_seen=event.ts,
            last_seen=event.ts,
            summary=_summary(event.module, event.event, event.detail),
            detail=dict(event.detail),
            suggested_action=_suggested_action(event.module, event.event, event.detail),
        )
        _open_cache[issue_id] = issue
        _append_issue_record(issue)
        return issue


def record_job_had_errors(
    *,
    module: str,
    job_type: str,
    job_id: str,
    status: str,
    had_errors: bool,
    detail: dict[str, Any] | None = None,
    ts: str = "",
) -> ObservabilityIssue | None:
    if not had_errors:
        return None
    payload = {
        "job_type": job_type,
        "job_id": job_id,
        "status": status,
        "had_errors": True,
        **(detail or {}),
    }
    event = ObservabilityEvent(
        module=module,  # type: ignore[arg-type]
        event="job_had_errors",
        level="error",
        job_id=job_id,
        detail=payload,
        ts=ts,
    )
    return record_issue_from_event(event)


def record_repeat_skip(*, agent_id: str, skip_reason: str, ts: str = "") -> ObservabilityIssue | None:
    key = f"{agent_id}:{skip_reason}"
    now = time.time()
    with _lock:
        window = _skip_window[key]
        window.append(now)
        while window and now - window[0] > SKIP_WINDOW_SECONDS:
            window.popleft()
        if len(window) < SKIP_REPEAT_THRESHOLD:
            return None
    event = ObservabilityEvent(
        module="watch",
        event="skip_reason_storm",
        level="warn",
        agent_id=agent_id,
        detail={"skip_reason": skip_reason, "agent_id": agent_id, "count": len(window)},
        ts=ts,
    )
    return _record_warn_issue(event)


def record_ingest_source_fail(*, source: str, error: str, ts: str = "") -> ObservabilityIssue | None:
    key = source
    now = time.time()
    with _lock:
        window = _ingest_fail_window[key]
        window.append(now)
        while window and now - window[0] > INGEST_WINDOW_SECONDS:
            window.popleft()
        if len(window) < INGEST_FAIL_THRESHOLD:
            return None
    event = ObservabilityEvent(
        module="ingest",
        event="source_fetch_failed",
        level="warn",
        detail={"source": source, "last_error": error, "count": len(window)},
        ts=ts,
    )
    return _record_warn_issue(event)


def _record_warn_issue(event: ObservabilityEvent) -> ObservabilityIssue:
    issue_id = _fingerprint(event.module, event.event, event.detail)
    with _lock:
        _load_open_from_disk()
        existing = _open_cache.get(issue_id)
        if existing:
            updated = replace(
                existing,
                last_seen=event.ts,
                count=existing.count + 1,
                detail={**existing.detail, **event.detail},
            )
            _open_cache[issue_id] = updated
            _append_issue_record(updated)
            return updated
        issue = ObservabilityIssue(
            issue_id=issue_id,
            severity="warn",
            module=event.module,
            event=event.event,
            first_seen=event.ts,
            last_seen=event.ts,
            summary=_summary(event.module, event.event, event.detail),
            detail=dict(event.detail),
            suggested_action=_suggested_action(event.module, event.event, event.detail),
        )
        _open_cache[issue_id] = issue
        _append_issue_record(issue)
        return issue


def list_issues(
    *,
    status: str | None = "open",
    module: str | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    with _lock:
        _load_open_from_disk()
        rows = list(_open_cache.values()) if status == "open" else []
    if status != "open":
        rows = []
        for row in reversed(read_jsonl_tail(issues_path(), limit=max(limit * 3, 300))):
            if status and row.get("status") != status:
                continue
            rows.append(
                ObservabilityIssue(
                    issue_id=str(row.get("issue_id") or ""),
                    severity=row.get("severity") or "error",
                    module=row.get("module") or "system",
                    event=row.get("event") or "unknown",
                    status=row.get("status") or "open",
                    first_seen=str(row.get("first_seen") or ""),
                    last_seen=str(row.get("last_seen") or ""),
                    count=int(row.get("count") or 1),
                    summary=str(row.get("summary") or ""),
                    detail=row.get("detail") if isinstance(row.get("detail"), dict) else {},
                    suggested_action=str(row.get("suggested_action") or ""),
                )
            )
    if module:
        rows = [r for r in rows if r.module == module]
    rows.sort(key=lambda r: r.last_seen, reverse=True)
    return [r.to_dict() for r in rows[:limit]]


def resolve_issue(issue_id: str) -> bool:
    with _lock:
        _load_open_from_disk()
        existing = _open_cache.pop(issue_id, None)
        if not existing:
            return False
        resolved = replace(existing, status="resolved")
        _append_issue_record(resolved)
        return True


def open_issue_count(*, module: str | None = None) -> int:
    with _lock:
        _load_open_from_disk()
        if module:
            return sum(1 for issue in _open_cache.values() if issue.module == module)
        return len(_open_cache)
