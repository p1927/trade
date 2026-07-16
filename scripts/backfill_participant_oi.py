#!/usr/bin/env python3
"""Backfill FII participant OI / PCR into factor store."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "integrations"))

from trade_integrations.dataflows.index_research.participant_oi_backfill import (  # noqa: E402
    backfill_participant_oi,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Backfill participant OI / PCR history")
    parser.add_argument("--days", type=int, default=365)
    parser.add_argument("--max-days", type=int, default=120, help="Cap trading days to fetch (rate limit)")
    parser.add_argument("--sleep", type=float, default=0.4)
    args = parser.parse_args()
    summary = backfill_participant_oi(days=args.days, max_days=args.max_days, sleep_seconds=args.sleep)
    print(summary)
    return 0 if summary.get("status") == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
