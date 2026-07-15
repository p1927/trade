#!/usr/bin/env python3
"""Run the company research pipeline for one or more tickers."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tradingagents"))

import trade_integrations  # noqa: F401

from trade_integrations.dataflows.company_research.aggregator import run_company_research
from trade_integrations.dataflows.company_research.format import format_research_report


def main() -> int:
    parser = argparse.ArgumentParser(description="Company research enrichment pipeline")
    parser.add_argument("ticker", help="Ticker symbol (e.g. RELIANCE, AAPL)")
    parser.add_argument("--days", type=int, default=14, help="Lookahead days for events")
    parser.add_argument("--json", action="store_true", help="Emit JSON instead of markdown")
    args = parser.parse_args()

    doc = run_company_research(args.ticker, lookahead_days=args.days)
    if args.json:
        print(json.dumps(asdict(doc), default=str, indent=2))
    else:
        print(format_research_report(doc))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
