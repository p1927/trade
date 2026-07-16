#!/usr/bin/env python3
"""One-shot NSE data repository backfill — headed browser, repo parquet + hub ingest."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tradingagents"))

try:
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env")
except ImportError:
    pass

import trade_integrations  # noqa: F401

from trade_integrations.nse_browser.chrome_bootstrap import ensure_chrome_or_warn
from trade_integrations.nse_browser.missions import run_all_missions, run_mission
from trade_integrations.nse_browser.repository import ingest_repository_to_hub, load_repo_dataset, repo_root
from trade_integrations.nse_browser.orchestrator import ingest_nse_repository


def _repo_summary() -> dict:
    summary: dict = {"repo_root": str(repo_root()), "datasets": {}}
    for dataset in ("fii_dii", "fpi", "bulk_deals", "delivery", "pe_pb"):
        frame = load_repo_dataset(dataset)
        if frame.empty:
            summary["datasets"][dataset] = {"rows": 0}
            continue
        entry = {"rows": len(frame)}
        if "date" in frame.columns:
            entry["start"] = str(frame["date"].min())
            entry["end"] = str(frame["date"].max())
        summary["datasets"][dataset] = entry
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description="Backfill NSE data repository (data/nse/)")
    parser.add_argument(
        "--all",
        action="store_true",
        help="Run all missions with full historical backfill",
    )
    parser.add_argument(
        "--mission",
        choices=["fii_dii_history", "fpi_nsdl", "market_archives"],
        help="Run a single mission with historical backfill",
    )
    parser.add_argument("--ingest-only", action="store_true", help="Sync repo parquet to hub only")
    parser.add_argument("--refresh-cookies", action="store_true", help="Bootstrap fresh nodriver cookies")
    parser.add_argument("--no-agent", action="store_true", help="Disable MiniMax agent fallback")
    parser.add_argument("--summary", action="store_true", help="Print repo row counts and exit")
    args = parser.parse_args()

    if args.summary:
        print(json.dumps(_repo_summary(), indent=2))
        return 0

    if args.ingest_only:
        payload = ingest_nse_repository()
        print(payload if isinstance(payload, str) else json.dumps(payload, indent=2))
        return 0

    ensure_chrome_or_warn()

    kwargs = {
        "refresh_cookies": args.refresh_cookies,
        "agent_fallback": not args.no_agent,
        "backfill_historical": True,
    }

    if args.all:
        result = run_all_missions(shared_browser=True, **kwargs)
    elif args.mission:
        result = run_mission(args.mission, **kwargs)
    else:
        parser.error("Specify --all, --mission, --ingest-only, or --summary")
        return 2

    ingest_repository_to_hub()
    out = {
        "mission_result": result,
        "ingested": ingest_repository_to_hub(),
        "repo": _repo_summary(),
    }
    print(json.dumps(out, indent=2, default=str))

    fii_rows = out["repo"]["datasets"].get("fii_dii", {}).get("rows", 0)
    ok = isinstance(result, dict) and result.get("status") in ("ok", "partial")
    if args.all:
        ok = ok or out["repo"]["datasets"].get("fii_dii", {}).get("rows", 0) > 0
    return 0 if ok or fii_rows > 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
