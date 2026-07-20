#!/usr/bin/env python3
"""Background worker for DataRouter backlog (tiered APIs + NSE missions)."""

from __future__ import annotations

import argparse
import logging
import os
import sys
import time

from trade_integrations.data_router import backlog, normalized_store
from trade_integrations.data_router.adapters.nse_mission import fetch_flows, mirror_flows_to_normalized
from trade_integrations.data_router.adapters.ohlcv import AdapterError, fetch_ohlcv
from trade_integrations.data_router.catalog import get_source_spec
from trade_integrations.data_router.types import FetchSpec

logger = logging.getLogger(__name__)


def _spec_satisfied(spec: FetchSpec) -> bool:
    data, _, hit = normalized_store.read(spec)
    if not hit:
        return False
    if hasattr(data, "empty"):
        return not data.empty
    return data is not None


def process_job(job: dict) -> str:
    """Process one backlog job. Returns: completed | requeued | failed."""
    spec = backlog.spec_from_job(job)
    source_id = str(job.get("source_id") or "")
    if not spec.domain or not source_id:
        backlog.mark_failed(job, "invalid job payload")
        return "failed"

    if _spec_satisfied(spec):
        backlog.mark_completed(job)
        return "completed"

    src_spec = get_source_spec(source_id)
    tier = src_spec.tier if src_spec else "free"

    try:
        if spec.domain == "ohlcv":
            frame = fetch_ohlcv(source_id, spec)
            normalized_store.write(spec, frame, source=source_id)
        elif spec.domain == "flows":
            frame = fetch_flows(source_id, spec)
            mirror_flows_to_normalized(spec, frame, source=source_id)
        else:
            backlog.mark_failed(job, f"unsupported domain {spec.domain}")
            return "failed"
    except AdapterError as exc:
        if exc.reason in ("budget_exhausted", "rate_limited"):
            backlog.requeue(job)
            return "requeued"
        backlog.mark_failed(job, str(exc))
        return "failed"
    except Exception as exc:
        backlog.mark_failed(job, str(exc))
        return "failed"

    backlog.mark_completed(job)
    return "completed"


def process_one() -> bool:
    job = backlog.dequeue_next()
    if job is None:
        return False
    outcome = process_job(job)
    logger.info(
        "job %s domain=%s source=%s -> %s",
        job.get("job_id"),
        (job.get("spec") or {}).get("domain"),
        job.get("source_id"),
        outcome,
    )
    return True


def run_loop(*, poll_sec: float = 5.0, max_jobs: int | None = None) -> int:
    processed = 0
    while True:
        if max_jobs is not None and processed >= max_jobs:
            break
        if process_one():
            processed += 1
            backlog.write_heartbeat(jobs_processed=processed)
            continue
        backlog.write_heartbeat(jobs_processed=processed)
        if max_jobs is not None:
            break
        time.sleep(poll_sec)
    return processed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="DataRouter backlog worker")
    parser.add_argument("--once", action="store_true", help="Process at most one job and exit")
    parser.add_argument("--max-jobs", type=int, default=None, help="Stop after N jobs (loop mode)")
    parser.add_argument(
        "--poll-sec",
        type=float,
        default=float(os.getenv("DATA_ROUTER_WORKER_POLL_SEC", "5")),
        help="Sleep between polls when idle",
    )
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    if args.once:
        process_one()
        return 0

    if args.max_jobs is not None:
        run_loop(poll_sec=args.poll_sec, max_jobs=args.max_jobs)
        return 0

    while True:
        run_loop(poll_sec=args.poll_sec, max_jobs=1)
        time.sleep(args.poll_sec)


if __name__ == "__main__":
    raise SystemExit(main())
