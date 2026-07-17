#!/usr/bin/env python3
"""Backfill distilled events parquet from legacy verified records."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "integrations") not in sys.path:
    sys.path.insert(0, str(ROOT / "integrations"))


def main() -> int:
    parser = argparse.ArgumentParser(description="Backfill hub news events from records.parquet")
    parser.add_argument("--ticker", default=None, help="Single ticker (default: all in records)")
    parser.add_argument("--days", type=int, default=None, help="Lookback days (default: env HUB_NEWS_MIGRATION_DAYS)")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--force", action="store_true", help="Re-upsert all rows, not only missing")
    args = parser.parse_args()

    from trade_integrations.env import load_trade_env
    from trade_integrations.hub_storage import news_migrations as migrations

    load_trade_env()

    if args.days is not None:
        import os

        os.environ["HUB_NEWS_MIGRATION_DAYS"] = str(args.days)
    if args.limit is not None:
        import os

        os.environ["HUB_NEWS_MIGRATION_LIMIT"] = str(args.limit)

    if args.ticker:
        summary = migrations.migrate_records_to_events(
            ticker=args.ticker,
            dry_run=args.dry_run,
            only_missing=not args.force,
        )
    else:
        summary = migrations.ensure_hub_news_migrations(
            dry_run=args.dry_run,
            force=args.force,
        )

    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
