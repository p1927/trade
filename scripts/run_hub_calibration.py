#!/usr/bin/env python3
"""Run unified hub calibration (morning / evening / both)."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "integrations") not in sys.path:
    sys.path.insert(0, str(ROOT / "integrations"))

from trade_integrations.env import load_trade_env
from trade_integrations.hub_analytics.calibration_orchestrator import (
    run_evening_hub_maintenance,
    run_morning_hub_calibration,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Unified hub calibration orchestrator")
    parser.add_argument(
        "--phase",
        choices=("morning", "evening", "all"),
        default="morning",
        help="Which calibration pipeline to run",
    )
    parser.add_argument("--dry-run", action="store_true", help="Report steps without executing")
    parser.add_argument("--force-retrain", action="store_true", help="Force index model retrain")
    parser.add_argument("--skip-retrain", action="store_true", help="Skip index model retrain")
    parser.add_argument("--json", action="store_true", help="Print summary JSON")
    args = parser.parse_args()

    load_trade_env()
    cfg = {
        "dry_run": args.dry_run,
        "force_retrain": args.force_retrain,
        "skip_retrain": args.skip_retrain,
    }

    if args.phase == "morning":
        summary = run_morning_hub_calibration(cfg)
    elif args.phase == "evening":
        summary = run_evening_hub_maintenance(cfg)
    else:
        summary = {
            "morning": run_morning_hub_calibration(cfg),
            "evening": run_evening_hub_maintenance(cfg),
        }

    if args.json:
        print(json.dumps(summary, indent=2, default=str))
    else:
        print(json.dumps(summary, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
