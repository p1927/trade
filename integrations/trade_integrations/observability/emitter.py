"""Structured event emitter — Tier 0 observability SSOT."""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import Any

from trade_integrations.observability.context import observability_context
from trade_integrations.observability.issues import (
    record_issue_from_event,
    record_job_had_errors,
    record_ingest_source_fail,
    record_repeat_skip,
)
from trade_integrations.observability.paths import events_path
from trade_integrations.observability.rollup import JobRollup
from trade_integrations.observability.schema import ObservabilityEvent, ObservabilityModule
from trade_integrations.observability.store import append_jsonl

logger = logging.getLogger(__name__)

_ENABLED_ENV = "TRADE_OBSERVABILITY_ENABLED"


def is_observability_enabled() -> bool:
    raw = os.getenv(_ENABLED_ENV, "1").strip().lower()
    return raw not in {"0", "false", "no", "off"}


def emit(
    module: ObservabilityModule,
    event: str,
    *,
    level: str = "info",
    duration_ms: int | None = None,
    detail: dict[str, Any] | None = None,
    **attrs: Any,
) -> ObservabilityEvent | None:
    """Append one structured event to events.jsonl and run issue hooks."""
    if not is_observability_enabled():
        return None

    ctx = observability_context()
    payload_detail = dict(detail or {})
    payload_detail.update({k: v for k, v in attrs.items() if v is not None})

    obs_event = ObservabilityEvent(
        module=module,
        event=event,
        level=level if level in {"info", "warn", "error"} else "info",  # type: ignore[arg-type]
        trace_id=str(attrs.get("trace_id") or ctx.trace_id or ""),
        agent_id=str(attrs.get("agent_id") or ctx.agent_id or payload_detail.get("agent_id") or ""),
        session_id=str(attrs.get("session_id") or ctx.session_id or ""),
        job_id=str(attrs.get("job_id") or ctx.job_id or payload_detail.get("job_id") or ""),
        ticker=str(attrs.get("ticker") or ctx.ticker or payload_detail.get("ticker") or ""),
        duration_ms=duration_ms,
        detail=payload_detail,
    )

    try:
        append_jsonl(events_path(), obs_event.to_dict())
    except OSError as exc:
        logger.warning("observability emit failed: %s", exc)
        return None

    if obs_event.level == "error":
        record_issue_from_event(obs_event)
    elif obs_event.event == "vibe_dispatch_skipped" and obs_event.detail.get("skip_reason"):
        record_repeat_skip(
            agent_id=obs_event.agent_id or str(obs_event.detail.get("agent_id") or "unknown"),
            skip_reason=str(obs_event.detail["skip_reason"]),
            ts=obs_event.ts,
        )
    elif obs_event.event == "source_fetch_failed" and obs_event.detail.get("source"):
        record_ingest_source_fail(
            source=str(obs_event.detail["source"]),
            error=str(obs_event.detail.get("error") or obs_event.detail.get("last_error") or ""),
            ts=obs_event.ts,
        )

    log_fn = logger.info
    if obs_event.level == "warn":
        log_fn = logger.warning
    elif obs_event.level == "error":
        log_fn = logger.error
    log_fn("[%s] %s %s", module, event, payload_detail)

    return obs_event


def emit_job_rollup(rollup: JobRollup, *, module: ObservabilityModule = "schedule") -> None:
    """Emit job completion rollup and open issues on silent failure."""
    ts = datetime.now(timezone.utc).isoformat()
    level = "info"
    if rollup.had_errors or rollup.silent_failure():
        level = "error"
    elif rollup.status == "partial":
        level = "warn"

    emit(
        module,
        "job_complete",
        level=level,  # type: ignore[arg-type]
        job_id=rollup.job_id,
        detail={
            "status": rollup.status,
            "had_errors": rollup.had_errors,
            "had_work": rollup.had_work,
            "children_failed": rollup.children_failed,
            "job_type": rollup.job_type,
            "expected_work": rollup.expected_work,
            "silent_failure": rollup.silent_failure(),
            **(rollup.detail or {}),
        },
    )

    if rollup.had_errors:
        record_job_had_errors(
            module=module,
            job_type=rollup.job_type,
            job_id=rollup.job_id,
            status=rollup.status,
            had_errors=True,
            detail=rollup.detail,
            ts=ts,
        )
    elif rollup.silent_failure():
        record_issue_from_event(
            ObservabilityEvent(
                module=module,
                event="silent_job_failure",
                level="error",
                job_id=rollup.job_id,
                detail={
                    "status": rollup.status,
                    "had_work": rollup.had_work,
                    "expected_work": rollup.expected_work,
                    "job_type": rollup.job_type,
                    **(rollup.detail or {}),
                },
                ts=ts,
            )
        )
