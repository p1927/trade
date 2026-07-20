#!/usr/bin/env python3
"""Bulk-fetch OpenAlgo / INDmoney daily OHLCV and persist to repo + hub + cold tier."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "integrations") not in sys.path:
    sys.path.insert(0, str(ROOT / "integrations"))


def main() -> int:
    parser = argparse.ArgumentParser(description="Bulk OpenAlgo history ingest")
    parser.add_argument("--bundle", default="nifty50", help="Symbol bundle (nifty50, nifty100, indices_extended, all)")
    parser.add_argument("--all-bundles", action="store_true", help="Run indices_extended + nifty50 + nifty100")
    parser.add_argument("--years", type=int, default=10, help="History depth (max 10 for INDmoney daily)")
    parser.add_argument("--end", dest="end_date", default=None, help="End date YYYY-MM-DD")
    parser.add_argument("--interval", default="D", help="Bar interval (D only for bulk)")
    parser.add_argument("--sleep", type=float, default=0.35, help="Seconds between API calls")
    parser.add_argument("--force", action="store_true", help="Re-fetch even when cache covers range")
    parser.add_argument("--no-historify", action="store_true", help="Skip historify.duckdb import")
    parser.add_argument("--no-cold-tier", action="store_true", help="Skip cold-tier panel sync")
    parser.add_argument("--symbol", action="append", dest="symbols", help="Override with explicit symbol(s)")
    parser.add_argument("--historify-only", action="store_true", help="Import historify.duckdb only")
    args = parser.parse_args()

    from trade_integrations.env import load_trade_env

    load_trade_env()

    from trade_integrations.openalgo.bulk_history_persist import (
        import_historify_duckdb,
        persist_all_openalgo_bundles,
        persist_openalgo_bulk,
    )

    if args.historify_only:
        result = import_historify_duckdb(interval=args.interval)
        print(json.dumps(result, indent=2, default=str))
        return 0

    if args.all_bundles:
        bundles = ["indices_extended", "nifty50", "nifty100"]
        result = persist_all_openalgo_bundles(
            bundles=bundles,
            years=min(args.years, 10),
            end_date=args.end_date,
            sleep_s=args.sleep,
            force=args.force,
            sync_cold_tier=not args.no_cold_tier,
        )
        print(json.dumps(result, indent=2, default=str))
        return 0

    result = persist_openalgo_bulk(
        bundle=args.bundle,
        years=min(args.years, 10),
        end_date=args.end_date,
        interval=args.interval,
        sleep_s=args.sleep,
        force=args.force,
        import_historify=not args.no_historify,
        sync_cold_tier=not args.no_cold_tier,
        symbols=args.symbols,
    )
    print(json.dumps(result, indent=2, default=str))
    return 0 if result.get("summary", {}).get("error", 0) == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
