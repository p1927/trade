#!/usr/bin/env python3
"""Query verified news from hub SSOT with tag / date / factor filters."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "integrations") not in sys.path:
    sys.path.insert(0, str(ROOT / "integrations"))


def _split_csv(value: str | None) -> list[str] | None:
    if not value:
        return None
    return [part.strip() for part in value.split(",") if part.strip()]


def main() -> int:
    parser = argparse.ArgumentParser(description="Filter verified news by tags, date, symbol, factor")
    parser.add_argument("--ticker", default="NIFTY")
    parser.add_argument("--since", help="YYYY-MM-DD inclusive")
    parser.add_argument("--until", help="YYYY-MM-DD inclusive")
    parser.add_argument("--day", help="Exact publish day YYYY-MM-DD")
    parser.add_argument("--symbols", help="Comma-separated symbols e.g. NIFTY,RELIANCE")
    parser.add_argument("--topics", help="Comma-separated topics e.g. oil,fii,war")
    parser.add_argument("--factors", help="Comma-separated factors e.g. fii_net_5d,oil_brent")
    parser.add_argument("--themes", help="Comma-separated themes e.g. crash,rally,selloff")
    parser.add_argument("--tags", help="Comma-separated flat tags e.g. factor:fii_net_5d,theme:crash")
    parser.add_argument("--status", help="Verification status filter (approved,partial,rejected)")
    parser.add_argument("--include-rejected", action="store_true")
    parser.add_argument("--limit", type=int, default=25)
    parser.add_argument("--inventory", action="store_true", help="Show tag vocabulary + used values")
    parser.add_argument("--repair-tags", action="store_true", help="Backfill tags on existing hub rows")
    args = parser.parse_args()

    from trade_integrations.env import load_trade_env

    load_trade_env()

    if args.repair_tags:
        from trade_integrations.dataflows.index_research.news_impact_engine import repair_hub_tags

        print(json.dumps(repair_hub_tags(ticker=args.ticker), indent=2))
        return 0

    if args.inventory:
        from trade_integrations.dataflows.news_hub_bridge import tag_inventory

        print(json.dumps(tag_inventory(ticker=args.ticker), indent=2))
        return 0

    from trade_integrations.dataflows.news_hub_bridge import query_verified_news

    status = args.status
    if status and "," in status:
        status = [s.strip() for s in status.split(",") if s.strip()]

    rows = query_verified_news(
        ticker=args.ticker,
        since=args.since,
        until=args.until,
        publish_day=args.day,
        symbols=_split_csv(args.symbols),
        topics=_split_csv(args.topics),
        factors=_split_csv(args.factors),
        themes=_split_csv(args.themes),
        tags=_split_csv(args.tags),
        status=status,
        include_rejected=args.include_rejected,
        limit=args.limit,
    )
    slim = [
        {
            "canonical_story_id": r.get("canonical_story_id"),
            "title": r.get("title"),
            "published_at": r.get("published_at"),
            "verification_status": r.get("verification_status"),
            "tags": r.get("tags"),
            "predicted_impact": r.get("predicted_impact"),
            "actual_impact": r.get("actual_impact"),
        }
        for r in rows
    ]
    print(json.dumps({"count": len(slim), "items": slim}, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
