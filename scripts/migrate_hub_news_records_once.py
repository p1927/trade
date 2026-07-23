#!/usr/bin/env python3
"""One-time cutover: copy legacy records.parquet → events.parquet, archive records."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "integrations") not in sys.path:
    sys.path.insert(0, str(ROOT / "integrations"))


def main() -> int:
    parser = argparse.ArgumentParser(
        description="One-time hub news SSOT cutover (records.parquet → events.parquet)"
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Run migration and archive legacy records (default is dry-run)",
    )
    parser.add_argument(
        "--rebuild-index",
        action="store_true",
        help="Rebuild event_index.parquet after migration",
    )
    args = parser.parse_args()

    from trade_integrations.env import load_trade_env
    from trade_integrations.hub_storage import news_migrations as migrations

    load_trade_env()
    dry_run = not args.apply

    if dry_run:
        print("Dry run — pass --apply to migrate and archive legacy records.parquet", file=sys.stderr)

    summary = migrations.finalize_events_ssot(dry_run=dry_run)
    if args.rebuild_index and not dry_run:
        from trade_integrations.hub_storage.news_event_index import rebuild_event_index

        summary["index_rebuild"] = rebuild_event_index()

    print(json.dumps(summary, indent=2))
    if dry_run:
        return 0
    if summary.get("legacy_rows") and not summary.get("archive", {}).get("archived"):
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
