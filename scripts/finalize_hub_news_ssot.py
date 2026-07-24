#!/usr/bin/env python3
"""One-shot ops: migrate legacy records.parquet → events SSOT and archive legacy file."""

from __future__ import annotations

import argparse
import json
import sys


def main() -> int:
    parser = argparse.ArgumentParser(description="Finalize hub news events SSOT cutover")
    parser.add_argument("--ticker", default=None, help="Optional ticker scope (default: all)")
    parser.add_argument("--dry-run", action="store_true", help="Report only; do not write")
    parser.add_argument("--force", action="store_true", help="Re-run incremental migration")
    args = parser.parse_args()

    from trade_integrations.env import load_trade_env

    load_trade_env()

    from trade_integrations.hub_storage.news_migrations import ensure_hub_news_migrations

    summary = ensure_hub_news_migrations(
        ticker=args.ticker,
        dry_run=args.dry_run,
        force=args.force,
    )
    print(json.dumps(summary, indent=2, default=str))
    legacy_remaining = int(summary.get("legacy_remaining") or 0)
    return 0 if legacy_remaining == 0 or args.dry_run else 1


if __name__ == "__main__":
    sys.exit(main())
