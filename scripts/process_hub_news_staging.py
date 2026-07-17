#!/usr/bin/env python3
"""Process hub news staging queue into distilled events."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "integrations") not in sys.path:
    sys.path.insert(0, str(ROOT / "integrations"))


def main() -> int:
    parser = argparse.ArgumentParser(description="Process hub news staging queue")
    parser.add_argument("--ticker", default="NIFTY")
    parser.add_argument("--limit", type=int, default=100)
    args = parser.parse_args()

    from trade_integrations.env import load_trade_env
    from trade_integrations.dataflows.index_research.news_entity_worker import process_staging_batch
    from trade_integrations.hub_storage.news_staging_store import staging_queue_stats

    load_trade_env()
    stats = staging_queue_stats(ticker=args.ticker)
    print(json.dumps({"queue_before": stats}, indent=2))
    result = process_staging_batch(ticker=args.ticker, limit=args.limit)
    stats_after = staging_queue_stats(ticker=args.ticker)
    print(json.dumps({"result": result, "queue_after": stats_after}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
