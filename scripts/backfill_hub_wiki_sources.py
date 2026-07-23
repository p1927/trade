#!/usr/bin/env python3
"""Backfill LLM-Wiki raw/source exports from events.parquet SSOT."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "integrations") not in sys.path:
    sys.path.insert(0, str(ROOT / "integrations"))


def main() -> int:
    parser = argparse.ArgumentParser(description="Backfill hub events to LLM-Wiki raw/sources/news/")
    parser.add_argument("--ticker", default="", help="Filter by ticker (default: all)")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--force", action="store_true", help="Re-export even when fingerprint matches")
    parser.add_argument("--no-rescan", action="store_true")
    parser.add_argument("--cleanup-orphans", action="store_true", default=True)
    parser.add_argument("--no-cleanup-orphans", action="store_false", dest="cleanup_orphans")
    args = parser.parse_args()

    from trade_integrations.env import load_trade_env
    from trade_integrations.dataflows.hub_wiki.compile import (
        cleanup_orphan_wiki_source_exports,
        compile_all_events_to_wiki,
        purge_json_wiki_sidecars,
    )

    load_trade_env()
    result = compile_all_events_to_wiki(
        ticker=args.ticker or None,
        dry_run=args.dry_run,
        force=args.force,
        rescan=not args.no_rescan,
    )
    if args.cleanup_orphans and not args.dry_run:
        cleanup = cleanup_orphan_wiki_source_exports(ticker=args.ticker or None, dry_run=False)
        result["cleanup"] = cleanup
    if not args.dry_run:
        result["purge_json"] = purge_json_wiki_sidecars(dry_run=False)
    print(json.dumps(result, indent=2, default=str))
    return 0 if result.get("ok", result.get("skipped")) else 1


if __name__ == "__main__":
    raise SystemExit(main())
