#!/usr/bin/env python3
"""Ingest historic_data drop folder + data/nse repo into cold-tier history store."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "integrations") not in sys.path:
    sys.path.insert(0, str(ROOT / "integrations"))


def main() -> int:
    parser = argparse.ArgumentParser(description="Ingest historic data into hub cold tier")
    parser.add_argument("--all", action="store_true", help="Repo seeds + cold tier + hub + panel")
    parser.add_argument("--repo-only", action="store_true", help="Sync data/nse seeds to repo parquet only")
    parser.add_argument("--cold-tier", action="store_true", help="Bridge repo to cold-tier history store")
    parser.add_argument("--hub-sync", action="store_true", help="Mirror repo parquet to hub nse_browser")
    parser.add_argument("--panel", action="store_true", help="Materialize NIFTY_2006_present panel")
    parser.add_argument("--start", default="2006-01-01")
    parser.add_argument("--end", default=None)
    parser.add_argument("--offline", action="store_true", help="Skip live HTTP fetches")
    parser.add_argument("--dry-run", action="store_true", help="Panel dry-run only when --panel set")
    args = parser.parse_args()

    run_all = args.all or not any(
        (args.repo_only, args.cold_tier, args.hub_sync, args.panel)
    )

    from trade_integrations.env import load_trade_env

    load_trade_env()

    results: dict[str, object] = {}

    if run_all or args.repo_only:
        from trade_integrations.nse_browser.repository import sync_all_repo_seed_layers

        results["repo"] = sync_all_repo_seed_layers(
            allow_live_fetch=not args.offline,
            enrich_days=365,
        )

    if run_all or args.cold_tier:
        from trade_integrations.dataflows.index_research.history_ingest import sync_repo_to_cold_tier

        results["cold_tier"] = sync_repo_to_cold_tier(
            start=args.start,
            end=args.end,
            include_macro_backfill=True,
            include_flow_backfill=True,
            allow_live_fetch=not args.offline,
        )

    if run_all or args.hub_sync:
        from trade_integrations.nse_browser.repository import ingest_repository_to_hub

        results["hub"] = ingest_repository_to_hub(
            allow_live_fetch=not args.offline,
            enrich_days=365,
        )

    if run_all or args.panel:
        from trade_integrations.dataflows.index_research.history_panel import materialize_panel

        results["panel"] = materialize_panel(
            start=args.start,
            end=args.end,
            dry_run=args.dry_run,
        )

    print(json.dumps(results, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
