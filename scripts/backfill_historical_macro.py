#!/usr/bin/env python3
"""Backfill macro + NIFTY OHLCV history (2006+) into hub cold tier."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "integrations") not in sys.path:
    sys.path.insert(0, str(ROOT / "integrations"))


def main() -> int:
    parser = argparse.ArgumentParser(description="Backfill historical macro + NIFTY OHLCV")
    parser.add_argument("--start", default="2006-01-01")
    parser.add_argument("--end", default=None)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--also-factor-store", action="store_true", help="Also write daily factor store snapshots")
    args = parser.parse_args()

    from trade_integrations.env import load_trade_env
    from trade_integrations.dataflows.index_research.sources.historical_macro import backfill_macro_history

    load_trade_env()
    result = backfill_macro_history(start=args.start, end=args.end, dry_run=args.dry_run)
    print(json.dumps(result, indent=2))

    if args.also_factor_store and not args.dry_run and result.get("status") == "ok":
        from trade_integrations.dataflows.index_research.factor_backfill import backfill_factor_history

        days = 5000
        factor_result = backfill_factor_history(days=days, start=args.start)
        print(json.dumps({"factor_store": factor_result}, indent=2))

    return 0 if result.get("status") in {"ok", "dry_run"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
