#!/usr/bin/env python3
"""Run T0 information audit for prediction misses."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tradingagents"))

import trade_integrations  # noqa: F401

from trade_integrations.dataflows.index_research.t0_information_audit import run_and_save_t0_audit


def main() -> int:
    parser = argparse.ArgumentParser(description="T0 information audit")
    parser.add_argument("--days", type=int, default=365)
    parser.add_argument("--horizon-days", type=int, default=14)
    parser.add_argument("--ticker", type=str, default="NIFTY")
    args = parser.parse_args()

    report = run_and_save_t0_audit(
        days=args.days,
        horizon_days=args.horizon_days,
        ticker=args.ticker,
    )
    print(json.dumps({"tag_counts": report.get("tag_counts"), "miss_count": report.get("miss_count")}, indent=2))
    if report.get("status") != "ok":
        print(report.get("message", "t0 audit failed"), file=sys.stderr)
        return 1
    print(f"\nSaved: reports/hub/{args.ticker.upper()}/index_research/t0_information_audit.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
