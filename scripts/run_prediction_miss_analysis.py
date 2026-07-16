#!/usr/bin/env python3
"""Run prediction miss root-cause analysis on walk-forward backtest eval rows."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tradingagents"))

import trade_integrations  # noqa: F401

from trade_integrations.dataflows.index_research.backtest_runner import run_and_save_backtest
from trade_integrations.dataflows.index_research.prediction_miss_analysis import run_and_save_miss_analysis


def main() -> int:
    parser = argparse.ArgumentParser(description="Prediction miss RCA")
    parser.add_argument("--days", type=int, default=365)
    parser.add_argument("--horizon-days", type=int, default=14)
    parser.add_argument("--ticker", type=str, default="NIFTY")
    parser.add_argument("--skip-backtest", action="store_true", help="Use cached backtest_latest.json")
    args = parser.parse_args()

    backtest = None
    if not args.skip_backtest:
        backtest = run_and_save_backtest(days=args.days, horizon_days=args.horizon_days)

    report = run_and_save_miss_analysis(
        days=args.days,
        horizon_days=args.horizon_days,
        ticker=args.ticker,
        backtest_report=backtest,
    )
    summary = report.get("summary") or {}
    print(json.dumps(summary, indent=2))
    if report.get("status") != "ok":
        print(report.get("message", "miss analysis failed"), file=sys.stderr)
        return 1
    print(f"\nMisses: {summary.get('miss_count')} / {report.get('eval_count')}")
    print(f"Saved: reports/hub/{args.ticker.upper()}/index_research/miss_analysis_latest.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
