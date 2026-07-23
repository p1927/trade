#!/usr/bin/env python3
"""Audit hub events SSOT vs LLM-Wiki source exports."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "integrations") not in sys.path:
    sys.path.insert(0, str(ROOT / "integrations"))


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit hub events vs LLM-Wiki source exports")
    parser.add_argument("--ticker", default="", help="Filter by ticker (default: all tickers)")
    parser.add_argument(
        "--cleanup-legacy",
        action="store_true",
        help="Remove deprecated llm-wiki/wiki/events/ and legacy sources/ layout",
    )
    parser.add_argument("--json", action="store_true", help="Emit JSON only")
    args = parser.parse_args()

    from trade_integrations.env import load_trade_env
    from trade_integrations.dataflows.hub_wiki.audit import audit_hub_wiki_sync

    load_trade_env()
    report = audit_hub_wiki_sync(
        ticker=args.ticker or None,
        run_legacy_cleanup=args.cleanup_legacy,
    )

    if args.json:
        print(json.dumps(report, indent=2, default=str))
    else:
        print(f"Active events: {report['events_active']}")
        print(f"Source .md files: {report['source_md_files']}")
        print(f"Covered: {report['covered']} ({report['coverage_pct']}%)")
        print(f"Missing export: {len(report['missing_export'])}")
        print(f"Stale export: {len(report['stale_export'])}")
        print(f"Orphan sources: {len(report['orphan_source_event_ids'])}")
        print(f"Orphan md slugs: {len(report.get('orphan_md_slugs') or [])}")
        print(f"JSON sidecars remaining: {report.get('json_sidecars_remaining', 0)}")
        legacy = report.get("legacy") or {}
        if not report.get("legacy_layout_clean"):
            print(
                "WARNING: legacy layout remains — "
                f"wiki/events={legacy.get('legacy_wiki_events_files', 0)} files, "
                f"sources/={legacy.get('legacy_sources_files', 0)} files"
            )
        probe = report.get("llm_wiki_probe") or {}
        print(f"LLM-Wiki probe: {'ok' if probe.get('ok') else 'FAIL'}")
        if legacy.get("unmigrated_records_parquet"):
            print("WARNING: unmigrated records.parquet rows remain")
        cleanup = report.get("legacy_cleanup") or {}
        if cleanup.get("removed_files"):
            print(f"Legacy cleanup removed: {cleanup['removed_files']} files")

    return 0 if report.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
