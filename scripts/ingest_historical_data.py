#!/usr/bin/env python3
"""Ingest historic_data drop folder + data/nse repo into cold-tier history store."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "integrations") not in sys.path:
    sys.path.insert(0, str(ROOT / "integrations"))


def _run_audit(*, write: bool = True) -> dict[str, object]:
    cmd = [sys.executable, str(ROOT / "scripts" / "audit_prediction_data.py")]
    if write:
        cmd.append("--write")
    proc = subprocess.run(cmd, cwd=str(ROOT), capture_output=True, text=True, check=False)
    payload: dict[str, object] = {"returncode": proc.returncode}
    if proc.stdout.strip():
        try:
            payload["report"] = json.loads(proc.stdout)
        except json.JSONDecodeError:
            payload["stdout"] = proc.stdout.strip()[:8000]
    if proc.stderr.strip():
        payload["stderr"] = proc.stderr.strip()[:4000]
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description="Ingest historic data into hub cold tier")
    parser.add_argument("--all", action="store_true", help="Repo seeds + cold tier + hub + panel")
    parser.add_argument("--repo-only", action="store_true", help="Sync data/nse seeds to repo parquet only")
    parser.add_argument("--cold-tier", action="store_true", help="Bridge repo to cold-tier history store")
    parser.add_argument("--hub-sync", action="store_true", help="Mirror repo parquet to hub nse_browser")
    parser.add_argument("--panel", action="store_true", help="Materialize NIFTY_2006_present panel")
    parser.add_argument(
        "--incremental",
        action="store_true",
        help="Recent-window cold-tier upsert + hub mirror (no macro/flow HTTP backfill)",
    )
    parser.add_argument(
        "--incremental-days",
        type=int,
        default=30,
        help="Lookback days for --incremental (default 30)",
    )
    parser.add_argument("--audit", action="store_true", help="Run prediction data audit after ingest")
    parser.add_argument("--start", default="2006-01-01")
    parser.add_argument("--end", default=None)
    parser.add_argument("--offline", action="store_true", help="Skip live HTTP fetches")
    parser.add_argument("--dry-run", action="store_true", help="Panel dry-run only when --panel set")
    args = parser.parse_args()

    run_all = args.all or not any(
        (args.repo_only, args.cold_tier, args.hub_sync, args.panel, args.incremental, args.audit)
    )

    from trade_integrations.env import load_trade_env

    load_trade_env()

    results: dict[str, object] = {}

    if args.incremental:
        from trade_integrations.dataflows.index_research.history_ingest import run_history_incremental_sync

        results["incremental"] = run_history_incremental_sync(
            days=args.incremental_days,
            explicit=True,
        )
    elif run_all or args.repo_only:
        from trade_integrations.nse_browser.repository import sync_all_repo_seed_layers

        results["repo"] = sync_all_repo_seed_layers(
            allow_live_fetch=not args.offline,
            enrich_days=365,
            explicit=True,
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
            explicit=True,
            skip_repo_sync=bool(results.get("repo")),
        )

    if run_all or args.panel:
        from trade_integrations.dataflows.index_research.history_panel import materialize_panel

        results["panel"] = materialize_panel(
            start=args.start,
            end=args.end,
            dry_run=args.dry_run,
        )

    if args.audit or (run_all and not args.dry_run):
        results["audit"] = _run_audit(write=True)

    print(json.dumps(results, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
