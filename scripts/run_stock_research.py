#!/usr/bin/env python3
"""Run stock trade plan pipeline for one or more tickers."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tradingagents"))

import trade_integrations  # noqa: F401

from trade_integrations.context.hub import save_stock_research
from trade_integrations.dataflows.stock_research.aggregator import run_stock_research
from trade_integrations.dataflows.stock_research.format import format_stock_report


def main() -> int:
    parser = argparse.ArgumentParser(description="Stock trade plan pipeline")
    parser.add_argument("ticker", nargs="?", help="Ticker or comma-separated list")
    parser.add_argument("--days", type=int, default=14, help="Event lookahead days")
    args = parser.parse_args()
    tickers = [t.strip() for t in (args.ticker or "RELIANCE").split(",") if t.strip()]
    for sym in tickers:
        doc = run_stock_research(sym, lookahead_days=args.days)
        path = save_stock_research(doc)
        print(format_stock_report(doc))
        print(f"\nSaved: {path}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
