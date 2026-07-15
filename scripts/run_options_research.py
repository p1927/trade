#!/usr/bin/env python3
"""Run the options research pipeline for one or more underlyings."""

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

from trade_integrations.context.hub import save_options_research
from trade_integrations.dataflows.options_research.aggregator import run_options_research
from trade_integrations.dataflows.options_research.format import format_options_report
from trade_integrations.dataflows.options_research.market import is_options_research_eligible


def main() -> int:
    parser = argparse.ArgumentParser(description="Options research enrichment pipeline")
    parser.add_argument(
        "ticker",
        nargs="?",
        help="Underlying symbol (e.g. NIFTY, RELIANCE) or comma-separated list",
    )
    parser.add_argument("--expiry", default=None, help="Expiry in DDMMMYY (e.g. 30JUL25)")
    parser.add_argument("--days", type=int, default=14, help="Lookahead days for events")
    parser.add_argument("--json", action="store_true", help="Emit JSON instead of markdown")
    args = parser.parse_args()

    if not args.ticker:
        parser.error("ticker is required")

    tickers = [t.strip() for t in args.ticker.split(",") if t.strip()]
    exit_code = 0
    for ticker in tickers:
        if not is_options_research_eligible(ticker):
            print(f"Skipping {ticker}: not eligible for options research", file=sys.stderr)
            exit_code = 1
            continue
        doc = run_options_research(
            ticker,
            expiry_date=args.expiry,
            lookahead_days=args.days,
        )
        hub_path = save_options_research(doc)
        if args.json:
            print(json.dumps(asdict(doc), default=str, indent=2))
        else:
            print(format_options_report(doc))
            print(f"\nSaved to hub: {hub_path}", file=sys.stderr)
        if len(tickers) > 1:
            print("\n" + "=" * 60 + "\n")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
