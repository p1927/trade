#!/usr/bin/env python3
"""Print DataRouter backlog, worker heartbeat, and tiered API status."""

from __future__ import annotations

import argparse
import json
import sys

from trade_integrations.data_router import get_status


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="DataRouter + tiered API status")
    parser.add_argument("--json", action="store_true", help="Emit JSON")
    args = parser.parse_args(argv)

    status = get_status()
    if args.json:
        print(json.dumps(status, indent=2))
        return 0

    print("DataRouter status")
    print("-" * 72)
    print(f"enabled:           {status.get('enabled')}")
    print(f"backlog_pending:   {status.get('backlog_pending', 0)}")
    hb = status.get("worker_heartbeat") or {}
    print(f"worker_heartbeat:  {hb.get('at') or '(none)'}  jobs={hb.get('jobs_processed', 0)}")

    tiered = status.get("tiered_api") or {}
    print("")
    print("Tiered API (UTC day)")
    print("-" * 72)
    for row in tiered.get("sources", []):
        cfg = "yes" if row.get("configured") else "no"
        print(
            f"{row['source']:16}  configured={cfg}  "
            f"calls={row.get('calls', 0)}/{row.get('limit', '?')}  "
            f"remaining={row.get('remaining', '?')}  "
            f"queue_depth={row.get('queue_depth', 0)}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
