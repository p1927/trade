#!/usr/bin/env python3
"""Backfill verified news hub records from archived daily news + miss dates."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "integrations") not in sys.path:
    sys.path.insert(0, str(ROOT / "integrations"))


def _miss_dates() -> list[str]:
    try:
        from trade_integrations.dataflows.index_research.prediction_miss_analysis import (
            load_miss_analysis_report,
        )

        report = load_miss_analysis_report("NIFTY") or {}
        return [
            str(m.get("prediction_date") or "")[:10]
            for m in (report.get("misses") or [])
            if m.get("prediction_date")
        ]
    except Exception:
        return []


def _archive_days(hub, days: int) -> list[str]:
    news_dir = hub / "_data" / "news" / "daily"
    if not news_dir.is_dir():
        return []
    paths = sorted(news_dir.glob("*.parquet"), reverse=True)
    out: list[str] = []
    for path in paths[:days]:
        out.append(path.stem[:10])
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description="Backfill verified news hub records")
    parser.add_argument("--days", type=int, default=90)
    parser.add_argument("--ticker", default="NIFTY")
    parser.add_argument("--horizon-days", type=int, default=14)
    parser.add_argument("--prioritize-miss-dates", action="store_true")
    parser.add_argument("--force-reverify", action="store_true")
    parser.add_argument("--repair-tags", action="store_true", help="Backfill tags on hub rows without re-verify")
    args = parser.parse_args()

    from trade_integrations.context.hub import get_hub_dir
    from trade_integrations.env import load_trade_env
    from trade_integrations.dataflows.index_research.news_impact_engine import ingest_headlines_for_day, repair_hub_tags

    load_trade_env()
    hub = get_hub_dir()
    if args.repair_tags:
        print(json.dumps(repair_hub_tags(ticker=args.ticker), indent=2))
        return 0

    days_set: set[str] = set(_archive_days(hub, args.days))
    if args.prioritize_miss_dates:
        days_set.update(_miss_dates())

    if not days_set:
        end = date.today()
        for i in range(args.days):
            days_set.add((end - timedelta(days=i)).isoformat())

    totals = {"days": 0, "ingested": 0, "cache_hits": 0, "tags_merged": 0, "verified": 0, "rejected": 0, "approved_ui": 0}
    for day in sorted(days_set):
        stats = ingest_headlines_for_day(
            ticker=args.ticker,
            horizon_days=args.horizon_days,
            day=day,
            headline_limit=20,
            force_reverify=args.force_reverify,
        )
        totals["days"] += 1
        for key in ("ingested", "cache_hits", "tags_merged", "verified", "rejected", "approved_ui"):
            totals[key] += int(stats.get(key) or 0)

    print(json.dumps({"status": "ok", **totals}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
