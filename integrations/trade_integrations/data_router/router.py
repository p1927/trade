"""DataRouter — unified fetch facade."""

from __future__ import annotations

import logging
import os
import time
from typing import Any

import pandas as pd

from trade_integrations.data_router import backlog, normalized_store
from trade_integrations.data_router.adapters.nse_mission import fetch_flows, mirror_flows_to_normalized
from trade_integrations.data_router.adapters.ohlcv import AdapterError, fetch_ohlcv
from trade_integrations.data_router.catalog import get_chain, get_fetch_mode, get_source_spec
from trade_integrations.data_router.types import FetchResult, FetchSpec, SourceAttempt

logger = logging.getLogger(__name__)


def data_router_enabled() -> bool:
    raw = os.getenv("DATA_ROUTER_ENABLED", "1").strip().lower()
    return raw not in ("0", "false", "no", "off")


def _check_batch_policy(source_id: str) -> bool:
    try:
        from trade_integrations.dataflows.company_research.fetch_policy import tiered_source_allowed

        return tiered_source_allowed(source_id)
    except ImportError:
        return True


def fetch(
    spec: FetchSpec,
    *,
    mode: str | None = None,
    allow_background: bool = False,
    wait_background: float = 0,
) -> FetchResult:
    """Fetch data: normalized hub → source chain → optional background enqueue."""
    if not data_router_enabled():
        return FetchResult(status="miss", attempts=[SourceAttempt("data_router", "skipped")])

    resolved_mode = mode or get_fetch_mode(spec.domain)
    if resolved_mode == "sequential" and spec.domain == "ohlcv":
        return _fetch_ohlcv_sequential(
            spec,
            allow_background=allow_background,
            wait_background=wait_background,
        )
    if resolved_mode == "sequential" and spec.domain == "flows":
        return _fetch_flows_sequential(
            spec,
            allow_background=allow_background,
            wait_background=wait_background,
        )
    return FetchResult(
        status="miss",
        attempts=[SourceAttempt(spec.domain, "skipped", error="mode not implemented")],
    )


def _fetch_ohlcv_sequential(
    spec: FetchSpec,
    *,
    allow_background: bool,
    wait_background: float,
) -> FetchResult:
    attempts: list[SourceAttempt] = []
    data, path, cache_hit = normalized_store.read(spec)
    if cache_hit and isinstance(data, pd.DataFrame) and not data.empty:
        return FetchResult(
            status="ok",
            data=data,
            source_id="hub",
            attempts=[SourceAttempt("normalized_store", "ok", has_data=True)],
            normalized_path=path,
            cache_hit=True,
        )

    chain = get_chain(spec.domain, spec.market)
    pending_job_id: str | None = None

    for source_id in chain:
        if not _check_batch_policy(source_id):
            attempts.append(
                SourceAttempt(source_id, "skipped", error="blocked by fetch policy")
            )
            continue
        src_spec = get_source_spec(source_id)
        if src_spec and spec.domain not in src_spec.domains:
            attempts.append(SourceAttempt(source_id, "skipped", error="domain mismatch"))
            continue

        call_spec = FetchSpec(
            domain=spec.domain,
            market=spec.market,
            symbol=spec.symbol,
            start=spec.start,
            end=spec.end,
            extra={**spec.extra, "_source_id": source_id},
        )

        try:
            frame = fetch_ohlcv(source_id, call_spec)
            if frame is None or frame.empty:
                attempts.append(SourceAttempt(source_id, "no_data"))
                continue
            norm_path = normalized_store.write(spec, frame, source=source_id)
            attempts.append(SourceAttempt(source_id, "ok", has_data=True))
            return FetchResult(
                status="ok",
                data=normalized_store.normalize_ohlcv_frame(frame, source=source_id),
                source_id=source_id,
                attempts=attempts,
                normalized_path=norm_path,
                cache_hit=False,
            )
        except AdapterError as exc:
            reason = exc.reason
            attempts.append(SourceAttempt(source_id, reason, error=str(exc)))
            if reason in ("budget_exhausted", "rate_limited") and allow_background:
                job_id, _ = backlog.enqueue(spec, source_id)
                pending_job_id = job_id
            continue
        except Exception as exc:
            attempts.append(SourceAttempt(source_id, "error", error=str(exc)))
            continue

    pending_job_id = pending_job_id or _enqueue_background_remainder(
        spec, chain, attempts, allow_background=allow_background
    )

    if pending_job_id and wait_background > 0:
        if backlog.wait_for_job(pending_job_id, timeout_sec=wait_background):
            data, path, hit = normalized_store.read(spec)
            if hit and isinstance(data, pd.DataFrame) and not data.empty:
                return FetchResult(
                    status="ok",
                    data=data,
                    source_id="hub",
                    attempts=attempts,
                    normalized_path=path,
                    pending_job_id=pending_job_id,
                    cache_hit=True,
                )

    return FetchResult(
        status="miss",
        attempts=attempts,
        pending_job_id=pending_job_id,
    )


def _enqueue_background_remainder(
    spec: FetchSpec,
    chain: list[str],
    attempts: list[SourceAttempt],
    *,
    allow_background: bool,
) -> str | None:
    if not allow_background:
        return None
    tried = {a.name for a in attempts}
    pending_job_id: str | None = None
    for source_id in chain:
        if source_id in tried:
            continue
        src_spec = get_source_spec(source_id)
        if src_spec and src_spec.tier not in ("tiered", "mission"):
            continue
        job_id, _ = backlog.enqueue(spec, source_id)
        pending_job_id = pending_job_id or job_id
    return pending_job_id


def _fetch_flows_sequential(
    spec: FetchSpec,
    *,
    allow_background: bool,
    wait_background: float,
) -> FetchResult:
    attempts: list[SourceAttempt] = []
    chain = get_chain(spec.domain, spec.market)
    pending_job_id: str | None = None

    for source_id in chain:
        if not _check_batch_policy(source_id):
            attempts.append(SourceAttempt(source_id, "skipped", error="blocked by fetch policy"))
            continue
        try:
            frame = fetch_flows(source_id, spec)
            if frame is None or frame.empty:
                attempts.append(SourceAttempt(source_id, "no_data"))
                continue
            norm_path = mirror_flows_to_normalized(spec, frame, source=source_id)
            attempts.append(SourceAttempt(source_id, "ok", has_data=True))
            return FetchResult(
                status="ok",
                data=frame,
                source_id=source_id,
                attempts=attempts,
                normalized_path=norm_path,
                cache_hit=False,
            )
        except AdapterError as exc:
            reason = exc.reason
            attempts.append(SourceAttempt(source_id, reason, error=str(exc)))
            if reason in ("budget_exhausted", "rate_limited") and allow_background:
                job_id, _ = backlog.enqueue(spec, source_id)
                pending_job_id = job_id
            continue
        except Exception as exc:
            attempts.append(SourceAttempt(source_id, "error", error=str(exc)))
            continue

    pending_job_id = pending_job_id or _enqueue_background_remainder(
        spec, chain, attempts, allow_background=allow_background
    )

    if pending_job_id and wait_background > 0:
        if backlog.wait_for_job(pending_job_id, timeout_sec=wait_background):
            data, path, hit = normalized_store.read(spec)
            if hit:
                return FetchResult(
                    status="ok",
                    data=data,
                    source_id="hub",
                    attempts=attempts,
                    normalized_path=path,
                    pending_job_id=pending_job_id,
                    cache_hit=True,
                )

    return FetchResult(status="miss", attempts=attempts, pending_job_id=pending_job_id)


def get_status() -> dict[str, Any]:
    from trade_integrations.tiered_api import get_status_all

    tiered = get_status_all()
    return {
        "enabled": data_router_enabled(),
        "backlog_pending": backlog.pending_count(),
        "worker_heartbeat": backlog.read_heartbeat(),
        "tiered_api": tiered,
    }
