#!/usr/bin/env python3
"""Backfill FII/DII + India VIX history into hub cold tier."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "integrations") not in sys.path:
    sys.path.insert(0, str(ROOT / "integrations"))


def main() -> int:
    parser = argparse.ArgumentParser(description="Backfill historical FII/DII + VIX")
    parser.add_argument("--start", default="2006-01-01")
    parser.add_argument("--end", default=None)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--offline", action="store_true", help="Skip live HTTP fetches")
    parser.add_argument("--fao-bulk", action="store_true", help="Bulk NSE FAO archive → flow_derivatives_daily")
    parser.add_argument("--fao-max-fetch", type=int, default=None, help="Limit FAO dates per run")
    parser.add_argument("--fao-sleep", type=float, default=0.35, help="Seconds between FAO archive requests")
    args = parser.parse_args()

    from trade_integrations.env import load_trade_env
    from trade_integrations.dataflows.index_research.sources.historical_flows import backfill_flow_history

    load_trade_env()

    if args.fao_bulk:
        from trade_integrations.dataflows.index_research.sources.nse_flow_derivatives_backfill import (
            backfill_nse_fao_to_cold_tier,
        )

        fao_result = backfill_nse_fao_to_cold_tier(
            start=args.start,
            end=args.end,
            sleep_s=args.fao_sleep,
            max_fetch=args.fao_max_fetch,
            dry_run=args.dry_run,
        )
        print(json.dumps({"fao_bulk": fao_result}, indent=2))
        if fao_result.get("status") not in {"ok", "dry_run"}:
            return 1

    result = backfill_flow_history(
        start=args.start,
        end=args.end,
        allow_live_fetch=not args.offline,
        dry_run=args.dry_run,
    )
    print(json.dumps(result, indent=2))
    return 0 if result.get("status") in {"ok", "dry_run"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
