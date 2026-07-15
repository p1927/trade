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

from trade_integrations.context.hub import save_company_research
from trade_integrations.dataflows.company_research.aggregator import (
    run_company_research,
    run_company_research_batch,
)
from trade_integrations.dataflows.company_research.batch import fetch_upcoming_india_results
from trade_integrations.dataflows.company_research.format import format_research_report


def main() -> int:
    parser = argparse.ArgumentParser(description="Company research enrichment pipeline")
    parser.add_argument(
        "ticker",
        nargs="?",
        help="Ticker symbol (e.g. RELIANCE, AAPL) or comma-separated list",
    )
    parser.add_argument("--days", type=int, default=14, help="Lookahead days for events")
    parser.add_argument("--json", action="store_true", help="Emit JSON instead of markdown")
    parser.add_argument(
        "--upcoming-results",
        action="store_true",
        help="List all India financial-results dates in window, then run pipeline per symbol",
    )
    parser.add_argument("--market", default="IN", help="Market filter for --upcoming-results")
    args = parser.parse_args()

    if args.upcoming_results:
        events = fetch_upcoming_india_results(lookahead_days=args.days)
        symbols = []
        seen: set[str] = set()
        for event in events:
            sym = event.get("symbol", "")
            if sym and sym not in seen:
                seen.add(sym)
                symbols.append(sym)
        if args.json:
            print(json.dumps({"upcoming_results": events, "symbols": symbols}, default=str, indent=2))
        else:
            print(f"# Upcoming India Financial Results ({args.days} days)\n")
            for event in events[:50]:
                print(
                    f"- {event.get('date')}: **{event.get('symbol')}** — "
                    f"{event.get('description', event.get('purpose', ''))[:80]}"
                )
            if len(events) > 50:
                print(f"\n_+ {len(events) - 50} more omitted._")
        if not symbols:
            return 0
        docs = run_company_research_batch(symbols[:20], lookahead_days=args.days)
        for doc in docs:
            save_company_research(doc)
            if not args.json:
                print("\n" + "=" * 60 + "\n")
                print(format_research_report(doc))
        return 0

    if not args.ticker:
        parser.error("ticker is required unless --upcoming-results is set")

    tickers = [t.strip() for t in args.ticker.split(",") if t.strip()]
    if len(tickers) > 1:
        docs = run_company_research_batch(tickers, lookahead_days=args.days)
        for doc in docs:
            save_company_research(doc)
            if args.json:
                print(json.dumps(asdict(doc), default=str, indent=2))
            else:
                print(format_research_report(doc))
                print("\n" + "=" * 60 + "\n")
        return 0

    doc = run_company_research(tickers[0], lookahead_days=args.days)
    hub_path = save_company_research(doc)
    if args.json:
        print(json.dumps(asdict(doc), default=str, indent=2))
    else:
        print(format_research_report(doc))
        print(f"\nSaved to hub: {hub_path}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
