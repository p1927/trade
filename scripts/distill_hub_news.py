#!/usr/bin/env python3
"""Run hub news distillation batch (staging queue → distilled events)."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "integrations") not in sys.path:
    sys.path.insert(0, str(ROOT / "integrations"))


def main() -> int:
    parser = argparse.ArgumentParser(description="Distill hub news staging queue")
    parser.add_argument("--ticker", default="NIFTY")
    parser.add_argument("--batch-size", type=int, default=100)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    from trade_integrations.env import load_trade_env
    from trade_integrations.dataflows.index_research.news_entity_worker import (
        run_hub_news_entity_job,
    )
    from trade_integrations.hub_storage.news_staging_store import (
        list_pending_refs,
        staging_queue_stats,
    )

    load_trade_env()
    before = staging_queue_stats(ticker=args.ticker)
    pending = list_pending_refs(ticker=args.ticker, limit=args.batch_size)
    preview = {
        "queue_before": before,
        "would_process": len(pending),
        "dry_run": args.dry_run,
    }
    print(json.dumps(preview, indent=2))
    if args.dry_run:
        return 0

    result = run_hub_news_entity_job(
        {"ticker": args.ticker, "batch_size": args.batch_size},
    )
    after = staging_queue_stats(ticker=args.ticker)
    print(json.dumps({"result": result, "queue_after": after}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
