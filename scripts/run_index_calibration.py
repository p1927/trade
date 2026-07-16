#!/usr/bin/env python3
"""Nightly index calibration — reconcile ledger and retrain on drift."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tradingagents"))

import trade_integrations  # noqa: F401

from trade_integrations.dataflows.index_research.calibration_runner import run_calibration


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Reconcile index prediction ledger and retrain model on drift",
    )
    parser.add_argument(
        "--horizon-days",
        type=int,
        default=None,
        help="Horizon profile for retrain (default env / 14)",
    )
    parser.add_argument(
        "--skip-retrain",
        action="store_true",
        help="Reconcile and score only; do not retrain",
    )
    parser.add_argument(
        "--force-retrain",
        action="store_true",
        help="Backfill factor history and retrain even without drift",
    )
    args = parser.parse_args()

    summary = run_calibration(
        horizon_days=args.horizon_days,
        skip_retrain=args.skip_retrain,
        force_retrain=args.force_retrain,
    )
    print(json.dumps(summary, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
