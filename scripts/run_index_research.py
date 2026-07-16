#!/usr/bin/env python3
"""Run the index research pipeline for Nifty or other index tickers."""

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

from trade_integrations.context.hub import save_index_research
from trade_integrations.dataflows.index_research.aggregator import run_index_research
from trade_integrations.dataflows.index_research.format import format_index_report


def main() -> int:
    parser = argparse.ArgumentParser(description="Index research enrichment pipeline")
    parser.add_argument(
        "ticker",
        nargs="?",
        default="NIFTY",
        help="Index ticker (default NIFTY)",
    )
    parser.add_argument(
        "--horizon-days",
        type=int,
        default=None,
        help="Prediction horizon in days (default from INDEX_RESEARCH_HORIZON_DAYS or 14)",
    )
    parser.add_argument(
        "--refresh-constituents",
        action="store_true",
        help="Force refresh company research for all constituents",
    )
    parser.add_argument("--json", action="store_true", help="Emit JSON instead of markdown")
    args = parser.parse_args()

    doc = run_index_research(
        args.ticker,
        horizon_days=args.horizon_days,
        refresh_constituents=args.refresh_constituents,
    )
    hub_path = save_index_research(doc)
    if args.json:
        payload = asdict(doc)
        payload["as_of"] = doc.as_of.isoformat()
        payload["stages"] = [
            {**asdict(stage), "fetched_at": stage.fetched_at.isoformat()} for stage in doc.stages
        ]
        print(json.dumps(payload, indent=2, default=str))
    else:
        print(format_index_report(doc))
        print(f"\nSaved to hub: {hub_path}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
