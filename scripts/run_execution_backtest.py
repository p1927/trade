#!/usr/bin/env python3
"""CLI: run execution simulation from track scoreboard eval rows."""

from __future__ import annotations

import argparse
import json
import sys


def main() -> int:
    parser = argparse.ArgumentParser(description="Run execution backtest from forecast tracks")
    parser.add_argument("--ticker", default="NIFTY")
    parser.add_argument("--track", default="quant_ridge")
    parser.add_argument(
        "--strategy",
        default="futures_trend",
        choices=("futures_trend", "mean_reversion", "options_spread"),
    )
    parser.add_argument("--no-persist", action="store_true")
    args = parser.parse_args()

    from trade_integrations.dataflows.index_research.execution_sim.runner import run_execution_backtest

    report = run_execution_backtest(
        ticker=args.ticker,
        track_id=args.track,
        strategy=args.strategy,
        persist=not args.no_persist,
    )
    print(json.dumps(report, indent=2))
    return 0 if report.get("status") == "ok" else 1


if __name__ == "__main__":
    sys.exit(main())
