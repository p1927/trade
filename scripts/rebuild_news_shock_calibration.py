#!/usr/bin/env python3
"""Rebuild news shock calibration from verified news + major events history."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "integrations") not in sys.path:
    sys.path.insert(0, str(ROOT / "integrations"))


def main() -> int:
    parser = argparse.ArgumentParser(description="Rebuild news shock calibration")
    parser.add_argument("--ticker", default="NIFTY")
    parser.add_argument("--horizon-days", type=int, default=14)
    args = parser.parse_args()

    from trade_integrations.env import load_trade_env
    from trade_integrations.dataflows.index_research.news_shock_calibration import (
        update_shock_calibration_from_history,
    )

    load_trade_env()
    result = update_shock_calibration_from_history(ticker=args.ticker, horizon_days=args.horizon_days)
    print(json.dumps(result, indent=2))
    eligible = sum(1 for t in (result.get("topics") or {}).values() if t.get("overlay_eligible"))
    return 0 if eligible > 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
