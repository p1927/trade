#!/usr/bin/env python3
"""Backfill company news for major Nifty drawdown dates."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "integrations"))

from trade_integrations.dataflows.index_research.company_news_backfill import backfill_drawdown_news  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Backfill news on major drawdown dates")
    parser.add_argument("--ticker", default="NIFTY")
    parser.add_argument("--sleep", type=float, default=0.25)
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args()
    summary = backfill_drawdown_news(
        ticker=args.ticker,
        sleep_seconds=args.sleep,
        overwrite=args.overwrite,
    )
    print(summary)
    return 0 if summary.get("status") == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
