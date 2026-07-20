#!/usr/bin/env python3
"""Backfill historical news event factors (curated calendar + optional GDELT)."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "integrations") not in sys.path:
    sys.path.insert(0, str(ROOT / "integrations"))


def main() -> int:
    parser = argparse.ArgumentParser(description="Backfill historical news event factors")
    parser.add_argument("--start", default="2006-01-01")
    parser.add_argument("--end", default=None)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--no-gdelt", action="store_true")
    parser.add_argument("--gdelt-max-days", type=int, default=0, help="0 = skip GDELT file crawl")
    args = parser.parse_args()

    from trade_integrations.env import load_trade_env
    from trade_integrations.dataflows.index_research.sources.historical_news import backfill_news_history

    load_trade_env()
    gdelt_max = None if args.gdelt_max_days <= 0 else args.gdelt_max_days
    result = backfill_news_history(
        start=args.start,
        end=args.end,
        use_gdelt=not args.no_gdelt,
        gdelt_max_days=gdelt_max,
        dry_run=args.dry_run,
    )
    print(json.dumps(result, indent=2))
    return 0 if result.get("status") in {"ok", "dry_run"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
