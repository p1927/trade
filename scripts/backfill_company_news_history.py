#!/usr/bin/env python3
"""Backfill retroactive company news archives for Nifty constituents."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "integrations"))

from trade_integrations.dataflows.index_research.company_news_backfill import (  # noqa: E402
    backfill_nifty_constituent_news,
)
from trade_integrations.dataflows.index_research.constituents import load_nifty50_constituents  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Backfill company news history via Google News RSS")
    parser.add_argument("--days", type=int, default=180, help="Calendar lookback for trading days")
    parser.add_argument("--symbols", type=str, default="", help="Comma-separated symbols (default: all Nifty 50)")
    parser.add_argument("--sleep", type=float, default=0.35, help="Seconds between RSS requests")
    parser.add_argument("--overwrite", action="store_true", help="Replace existing history files")
    args = parser.parse_args()

    symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()] or None
    if symbols is None:
        symbols = [row.symbol for row in load_nifty50_constituents()]

    print(f"Backfilling {len(symbols)} symbols over ~{args.days} days…")
    summary = backfill_nifty_constituent_news(
        days=args.days,
        symbols=symbols,
        sleep_seconds=args.sleep,
        overwrite=args.overwrite,
    )
    print(summary)
    return 0 if summary.get("status") == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
