#!/usr/bin/env python3
"""Print tiered API queue budget + cache status."""

from __future__ import annotations

import argparse
import json
import sys

from trade_integrations.tiered_api import get_status_all


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Tiered API queue + hub cache status")
    parser.add_argument("--json", action="store_true", help="Emit JSON")
    args = parser.parse_args(argv)

    status = get_status_all()
    if args.json:
        print(json.dumps(status, indent=2))
        return 0

    print("Tiered API status (UTC day)")
    print("-" * 72)
    for row in status.get("sources", []):
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
