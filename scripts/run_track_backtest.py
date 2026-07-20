#!/usr/bin/env python3
"""CLI: run per-track walk-forward backtest and write scoreboard."""

from __future__ import annotations

import argparse
import json
import os
import sys


def main() -> int:
    parser = argparse.ArgumentParser(description="Run forecast track backtest")
    parser.add_argument("--ticker", default="NIFTY")
    parser.add_argument("--days", type=int, default=365)
    parser.add_argument("--horizon-days", type=int, default=None)
    parser.add_argument("--eval-step", type=int, default=5)
    parser.add_argument("--min-train-rows", type=int, default=40)
    parser.add_argument(
        "--include-ml-tracks",
        action="store_true",
        help="Set INDEX_PREDICTION_ML_WALKFORWARD=1 for experimental ML tracks",
    )
    args = parser.parse_args()

    if args.include_ml_tracks:
        os.environ["INDEX_PREDICTION_ML_WALKFORWARD"] = "1"

    from trade_integrations.dataflows.index_research.prediction_algorithms.evaluator.walk_forward import (
        run_track_walk_forward,
    )

    report = run_track_walk_forward(
        ticker=args.ticker,
        days=args.days,
        horizon_days=args.horizon_days,
        min_train_rows=args.min_train_rows,
        eval_step=args.eval_step,
    )
    print(json.dumps(report, indent=2))
    return 0 if report.get("status") == "ok" else 1


if __name__ == "__main__":
    sys.exit(main())
