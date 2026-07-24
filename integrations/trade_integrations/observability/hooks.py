"""Reusable Tier 0 instrumentation hooks (safe no-op when disabled)."""

from __future__ import annotations

import time
from contextlib import contextmanager
from typing import Any, Iterator

from trade_integrations.observability.schema import ObservabilityModule


def safe_emit(
    module: ObservabilityModule,
    event: str,
    *,
    level: str = "info",
    **attrs: Any,
) -> None:
    try:
        from trade_integrations.observability.emitter import emit

        emit(module, event, level=level, **attrs)  # type: ignore[arg-type]
    except ImportError:
        return


def bridge_pipeline_entry(entry: Any) -> None:
    try:
        from trade_integrations.observability.bridge_pipeline import pipeline_log_to_observability

        pipeline_log_to_observability(entry)
    except ImportError:
        return


def emit_pipeline_job_done(
    *,
    job_type: str,
    job_id: str,
    ticker: str,
    status: str,
    had_errors: bool = False,
    had_work: bool = True,
    duration_ms: int | None = None,
    detail: dict[str, Any] | None = None,
) -> None:
    try:
        from trade_integrations.observability.emitter import emit, emit_job_rollup
        from trade_integrations.observability.rollup import JobRollup

        payload = dict(detail or {})
        rollup = JobRollup(
            status=status,
            had_errors=had_errors,
            had_work=had_work,
            job_type=job_type,
            job_id=job_id,
            detail=payload,
        )
        emit_job_rollup(rollup, module="pipeline")
        level = "error" if had_errors or rollup.silent_failure() else "info"
        emit(
            "pipeline",
            f"{job_type}_job_done",
            level=level,  # type: ignore[arg-type]
            job_id=job_id,
            ticker=ticker,
            duration_ms=duration_ms,
            detail={"status": status, "had_errors": had_errors, **payload},
        )
    except ImportError:
        return


def emit_autonomous_watch_tick(agent_id: str, result: dict[str, Any]) -> None:
    status = str(result.get("status") or ("skipped" if result.get("skipped") else "ok"))
    level = "info"
    if status in {"error", "degraded"} or result.get("reason") == "nautilus_bridge_failed":
        level = "error"
    elif result.get("skipped") or status == "watch_only":
        level = "warn"
    safe_emit(
        "watch",
        "autonomous_watch_tick",
        level=level,  # type: ignore[arg-type]
        agent_id=agent_id,
        detail={
            "status": status,
            "reason": result.get("reason"),
            "watch_path": result.get("watch_path"),
            "delegated_to_detached": result.get("delegated_to_detached"),
            "requires_action": (result.get("feedback") or {}).get("requires_action")
            if isinstance(result.get("feedback"), dict)
            else None,
        },
    )


def emit_autonomous_decision(agent_id: str, entry: dict[str, Any]) -> None:
    decision = str(entry.get("decision") or "")
    level = "warn" if decision in {"EXIT", "REVISE"} else "info"
    safe_emit(
        "watch",
        "autonomous_decision",
        level=level,  # type: ignore[arg-type]
        agent_id=agent_id,
        detail={
            "decision": decision,
            "confidence": entry.get("confidence"),
            "strategy": entry.get("strategy"),
            "ticker": entry.get("ticker"),
        },
    )


def emit_ingest_complete(
    *,
    ticker: str,
    mode: str,
    sources: dict[str, Any],
    totals: dict[str, Any],
    blocked: bool = False,
) -> None:
    error_count = int(totals.get("error") or 0)
    level = "error" if blocked else ("warn" if error_count > 0 else "info")
    safe_emit(
        "ingest",
        "hub_news_ingest_complete",
        level=level,  # type: ignore[arg-type]
        ticker=ticker,
        detail={"mode": mode, "sources": sources, "totals": totals, "blocked": blocked},
    )
    for source, stats in sources.items():
        if isinstance(stats, dict) and stats.get("error"):
            safe_emit(
                "ingest",
                "source_fetch_failed",
                level="warn",
                ticker=ticker,
                source=source,
                error=str(stats.get("error"))[:300],
            )


def emit_entity_worker_complete(ticker: str, result: dict[str, Any]) -> None:
    had_errors = bool(result.get("had_errors"))
    safe_emit(
        "ingest",
        "news_entity_worker_complete",
        level="error" if had_errors else "info",
        ticker=ticker,
        detail={
            "had_errors": had_errors,
            "rollup": result.get("rollup"),
            "compact_events": result.get("compact_events") if isinstance(result.get("compact_events"), dict) else None,
        },
    )
    if had_errors:
        try:
            from trade_integrations.observability.emitter import emit_job_rollup
            from trade_integrations.observability.rollup import JobRollup

            emit_job_rollup(
                JobRollup(
                    status="partial",
                    had_errors=True,
                    had_work=True,
                    job_type="news_entity_worker",
                    job_id=f"{ticker}:entity",
                    detail={"ticker": ticker},
                ),
                module="ingest",
            )
        except ImportError:
            pass


@contextmanager
def llm_call_span(
    *,
    provider: str,
    model: str,
    tier: str = "vibe",
    session_id: str = "",
    agent_id: str = "",
) -> Iterator[dict[str, Any]]:
    started = time.monotonic()
    meta: dict[str, Any] = {
        "provider": provider,
        "model": model,
        "tier": tier,
    }
    extra: dict[str, Any] = {}
    try:
        yield extra
    except Exception as exc:
        safe_emit(
            "llm",
            "llm_call_failed",
            level="error",
            session_id=session_id,
            agent_id=agent_id,
            duration_ms=int((time.monotonic() - started) * 1000),
            detail={**meta, "error": str(exc)[:400]},
        )
        raise
    else:
        safe_emit(
            "llm",
            "llm_call_complete",
            level="info",
            session_id=session_id,
            agent_id=agent_id,
            duration_ms=int((time.monotonic() - started) * 1000),
            detail={**meta, **extra},
        )


def emit_react_iteration(
    *,
    iteration: int,
    max_iterations: int,
    session_id: str = "",
) -> None:
    level = "info"
    if iteration >= max_iterations:
        level = "error"
    elif iteration >= max(1, int(max_iterations * 0.8)):
        level = "warn"
    else:
        return
    safe_emit(
        "llm",
        "react_iteration_threshold",
        level=level,  # type: ignore[arg-type]
        session_id=session_id,
        detail={"iteration": iteration, "max_iterations": max_iterations},
    )


def emit_watch_signal(
    *,
    agent_id: str,
    signal: str,
    symbol: str = "",
    message: str = "",
) -> None:
    safe_emit(
        "watch",
        "watch_signal",
        level="warn",
        agent_id=agent_id,
        detail={"signal": signal, "symbol": symbol, "message": message[:300]},
    )


def emit_vibe_dispatch_skipped(
    *,
    agent_id: str,
    skip_reason: str,
    symbol: str = "",
    signal: str = "",
) -> None:
    """Per-alert skip telemetry; feeds skip-storm detection via emitter hooks."""
    safe_emit(
        "watch",
        "vibe_dispatch_skipped",
        level="warn",
        agent_id=agent_id,
        skip_reason=skip_reason,
        symbol=symbol,
        signal=signal,
    )


def emit_poll_tick(
    *,
    agent_id: str | None,
    alert_count: int,
    dispatch_count: int,
    skipped_outside_hours: int = 0,
) -> None:
    level = "info"
    if alert_count > 0:
        level = "warn"
    safe_emit(
        "watch",
        "poll_tick",
        level=level,  # type: ignore[arg-type]
        agent_id=agent_id or "",
        detail={
            "alert_count": alert_count,
            "dispatch_count": dispatch_count,
            "skipped_outside_hours": skipped_outside_hours,
        },
    )


def emit_full_reasoning_dispatch(
    *,
    agent_id: str,
    turn_kind: str,
    dispatched: bool,
    reason: str = "",
) -> None:
    safe_emit(
        "watch",
        "full_reasoning_dispatch",
        level="info" if dispatched else "warn",
        agent_id=agent_id,
        detail={"turn_kind": turn_kind, "dispatched": dispatched, "reason": reason},
    )


def emit_watch_registry_event(event: str, *, watch_id: str = "", detail: dict[str, Any] | None = None) -> None:
    safe_emit("watch", event, level="info", detail={"watch_id": watch_id, **(detail or {})})

