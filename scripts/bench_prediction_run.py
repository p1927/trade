#!/usr/bin/env python3
"""Benchmark manual index-prediction Run analysis — stage timing from pipeline_log."""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from typing import Any


def _api_base() -> str:
    import os

    return os.getenv("VIBE_API_BASE", "http://127.0.0.1:8899").rstrip("/")


def _post_json(path: str, body: dict[str, Any]) -> dict[str, Any]:
    url = f"{_api_base()}{path}"
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _get_json(path: str) -> dict[str, Any]:
    url = f"{_api_base()}{path}"
    req = urllib.request.Request(url, method="GET")
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _parse_iso(ts: str | None) -> float | None:
    if not ts:
        return None
    try:
        return datetime.fromisoformat(str(ts).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def _stage_timing_table(logs: list[dict[str, Any]]) -> list[tuple[str, str, float | None]]:
    """Return rows (stage, message, elapsed_ms) for completion log lines only."""
    rows: list[tuple[str, str, float | None]] = []
    seen: set[tuple[str, str]] = set()
    for entry in logs:
        detail = entry.get("detail") or {}
        elapsed = detail.get("elapsed_ms")
        if elapsed is None:
            continue
        key = (str(entry.get("stage") or ""), str(entry.get("message") or ""))
        if key in seen:
            continue
        seen.add(key)
        rows.append((key[0], key[1], float(elapsed)))
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(description="Benchmark index prediction Run analysis job")
    parser.add_argument("--ticker", default="NIFTY")
    parser.add_argument("--horizon-days", type=int, default=14)
    parser.add_argument("--refresh-constituents", action="store_true")
    parser.add_argument("--no-forecast-lab", action="store_true")
    parser.add_argument("--poll-seconds", type=float, default=2.0)
    parser.add_argument("--timeout-seconds", type=float, default=2700.0)
    args = parser.parse_args()

    body = {
        "ticker": args.ticker,
        "horizon_days": args.horizon_days,
        "refresh_constituents": args.refresh_constituents,
        "run_forecast_lab": not args.no_forecast_lab,
    }

    started = time.time()
    try:
        start = _post_json("/trade/index-prediction/run/start", body)
    except urllib.error.URLError as exc:
        print(f"Failed to start job: {exc}", file=sys.stderr)
        return 1

    job_id = str(start.get("job_id") or "")
    if not job_id:
        print(f"Unexpected start response: {start}", file=sys.stderr)
        return 1

    print(f"Started job {job_id} (status={start.get('job_status')})")

    last_log_count = 0
    while time.time() - started < args.timeout_seconds:
        try:
            payload = _get_json(f"/trade/index-prediction/run/{job_id}")
        except urllib.error.URLError as exc:
            print(f"Poll failed: {exc}", file=sys.stderr)
            time.sleep(args.poll_seconds)
            continue
        job = payload.get("job") or {}
        status = str(job.get("status") or "")
        logs = list(job.get("logs") or [])
        if len(logs) > last_log_count:
            for entry in logs[last_log_count:]:
                stage = entry.get("stage")
                msg = entry.get("message")
                detail = entry.get("detail") or {}
                elapsed = detail.get("elapsed_ms")
                suffix = f" ({elapsed}ms)" if elapsed is not None else ""
                print(f"  [{stage}] {msg}{suffix}")
            last_log_count = len(logs)

        if status in {"done", "error"}:
            wall_s = time.time() - started
            print(f"\nJob {status} in {wall_s:.1f}s wall-clock")
            if status == "error":
                print(f"Error: {job.get('error')}", file=sys.stderr)
                return 1
            print("\nStage timing (elapsed_ms from pipeline_log):")
            print(f"{'Stage':<18} {'Seconds':>8}  Message")
            print("-" * 72)
            total_ms = 0.0
            for stage, message, elapsed_ms in _stage_timing_table(logs):
                total_ms += elapsed_ms or 0.0
                print(f"{stage:<18} {(elapsed_ms or 0) / 1000:>8.1f}  {message[:40]}")
            print("-" * 72)
            print(f"{'SUM (timed stages)':<18} {total_ms / 1000:>8.1f}")
            created = _parse_iso(str(job.get("created_at") or ""))
            if created:
                print(f"Created at: {datetime.fromtimestamp(created, tz=timezone.utc).isoformat()}")
            return 0

        time.sleep(args.poll_seconds)

    print(f"Timed out after {args.timeout_seconds}s", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
