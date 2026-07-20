#!/usr/bin/env python3
"""Run unified hub news ingest (RSS, SearXNG, Moneycontrol, watcher)."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INTEGRATIONS = ROOT / "integrations"
if INTEGRATIONS.is_dir() and str(INTEGRATIONS) not in sys.path:
    sys.path.insert(0, str(INTEGRATIONS))


def main() -> int:
    parser = argparse.ArgumentParser(description="Ingest live news into hub staging")
    parser.add_argument("--ticker", default="NIFTY")
    parser.add_argument(
        "--sources",
        default="all",
        help="Comma-separated: rss,searxng,searxng_global,moneycontrol,watcher or all",
    )
    parser.add_argument("--lookback-days", type=int, default=None)
    parser.add_argument(
        "--mode",
        choices=("full", "light"),
        default=None,
        help="Ingest profile: full (daily) or light (RSS/watcher); uses pipeline config when omitted",
    )
    parser.add_argument(
        "--rss-limit-per-feed",
        type=int,
        default=10,
        help="Max RSS entries queued per feed (light runs often use 1–3)",
    )
    parser.add_argument(
        "--watcher-since-hours",
        type=int,
        default=6,
        help="Watcher lookback window when watcher source is enabled",
    )
    args = parser.parse_args()

    from trade_integrations.dataflows.index_research.hub_news_ingest import run_hub_news_ingest

    sources = args.sources if args.sources != "all" else "all"
    result = run_hub_news_ingest(
        ticker=args.ticker,
        sources=sources,
        mode=args.mode,
        lookback_days=args.lookback_days,
        rss_limit_per_feed=args.rss_limit_per_feed,
        watcher_since_hours=args.watcher_since_hours,
    )
    print(json.dumps(result, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
