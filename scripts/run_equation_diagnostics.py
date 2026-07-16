#!/usr/bin/env python3
"""Run equation diagnostics (block ablation, sign conflicts, regime corr)."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tradingagents"))

import trade_integrations  # noqa: F401

from trade_integrations.dataflows.index_research.equation_diagnostics import run_and_save_diagnostics


def main() -> int:
    parser = argparse.ArgumentParser(description="Equation diagnostics")
    parser.add_argument("--days", type=int, default=365)
    parser.add_argument("--horizon-days", type=int, default=14)
    parser.add_argument("--ticker", type=str, default="NIFTY")
    args = parser.parse_args()

    report = run_and_save_diagnostics(
        days=args.days,
        horizon_days=args.horizon_days,
        ticker=args.ticker,
    )
    print(json.dumps(
        {
            "baseline_hit_rate": report.get("baseline_direction_hit_rate"),
            "block_ablation": report.get("block_ablation"),
            "sign_conflicts": len(report.get("sign_conflicts") or []),
        },
        indent=2,
    ))
    if report.get("status") != "ok":
        print(report.get("message", "diagnostics failed"), file=sys.stderr)
        return 1
    print(f"\nSaved: reports/hub/{args.ticker.upper()}/index_research/equation_diagnostics_latest.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
