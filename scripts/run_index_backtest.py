#!/usr/bin/env python3
"""Run walk-forward index prediction backtest on historical factor + Nifty data."""

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


def main() -> int:
    parser = argparse.ArgumentParser(description="Index prediction historical backtest")
    parser.add_argument("--days", type=int, default=180, help="History window in calendar days")
    parser.add_argument("--horizon-days", type=int, default=14, help="Forward return horizon")
    parser.add_argument("--eval-step", type=int, default=5, help="Evaluate every N trading rows")
    parser.add_argument("--min-train", type=int, default=45, help="Minimum training rows before first eval")
    parser.add_argument(
        "--include-bottom-up",
        action="store_true",
        help="Replay hybrid bottom-up when company_research/history archives exist",
    )
    args = parser.parse_args()

    report = run_and_save_backtest(
        days=args.days,
        horizon_days=args.horizon_days,
        eval_step=args.eval_step,
        min_train_rows=args.min_train,
        include_bottom_up=args.include_bottom_up,
    )
    print(json.dumps(report.get("metrics", {}), indent=2))
    if report.get("status") != "ok":
        print(report.get("message", "backtest failed"), file=sys.stderr)
        return 1
    print(f"\nEvaluations: {report.get('eval_count')} rows from {report.get('history_start')} to {report.get('history_end')}")
    print(f"Saved: reports/hub/NIFTY/index_research/backtest_latest.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
