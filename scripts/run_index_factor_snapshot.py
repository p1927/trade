#!/usr/bin/env python3
"""Collect daily index macro + constituent aggregate factors into the factor store."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tradingagents"))

import trade_integrations  # noqa: F401

from trade_integrations.dataflows.index_research.snapshot import run_snapshot


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Collect daily Nifty index macro and constituent aggregate factors",
    )
    parser.add_argument(
        "--date",
        default=datetime.now(timezone.utc).date().isoformat(),
        help="Snapshot date (YYYY-MM-DD, default today UTC)",
    )
    parser.add_argument(
        "--skip-constituents",
        action="store_true",
        help="Skip constituent research; macro factors only",
    )
    args = parser.parse_args()

    summary = run_snapshot(
        snapshot_date=args.date,
        skip_constituents=args.skip_constituents,
    )
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
