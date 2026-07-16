#!/usr/bin/env python3
"""Run counterfactual decomposition on walk-forward backtest eval rows."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tradingagents"))

import trade_integrations  # noqa: F401

from trade_integrations.dataflows.index_research.prediction_counterfactual import run_and_save_counterfactual


def main() -> int:
    parser = argparse.ArgumentParser(description="Prediction counterfactual decomposition")
    parser.add_argument("--days", type=int, default=365)
    parser.add_argument("--horizon-days", type=int, default=14)
    parser.add_argument("--ticker", type=str, default="NIFTY")
    parser.add_argument("--skip-backtest", action="store_true")
    args = parser.parse_args()

    report = run_and_save_counterfactual(
        days=args.days,
        horizon_days=args.horizon_days,
        ticker=args.ticker,
        backtest_report=None if not args.skip_backtest else __import__(
            "trade_integrations.dataflows.index_research.backtest_runner",
            fromlist=["load_backtest_report"],
        ).load_backtest_report(args.ticker),
    )
    summary = report.get("summary") or {}
    print(json.dumps(summary, indent=2))
    if report.get("status") != "ok":
        print(report.get("message", "counterfactual failed"), file=sys.stderr)
        return 1
    print(f"\nEval rows: {report.get('eval_count')}")
    print(f"Saved: reports/hub/{args.ticker.upper()}/index_research/counterfactual_latest.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
