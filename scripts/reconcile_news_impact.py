#!/usr/bin/env python3
"""Reconcile matured news impact rows with actual Nifty moves."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "integrations") not in sys.path:
    sys.path.insert(0, str(ROOT / "integrations"))


def main() -> int:
    parser = argparse.ArgumentParser(description="Reconcile news impact at maturity")
    parser.add_argument("--ticker", default="NIFTY")
    parser.add_argument("--as-of", default=None, help="YYYY-MM-DD (default today UTC)")
    args = parser.parse_args()

    from trade_integrations.env import load_trade_env
    from trade_integrations.dataflows.index_research.news_impact_engine import (
        build_news_impact_snapshot,
        reconcile_matured_impacts,
        save_news_impact_snapshot,
    )

    load_trade_env()
    result = reconcile_matured_impacts(as_of=args.as_of, ticker=args.ticker)
    print(json.dumps(result, indent=2))
    if result.get("status") == "ok" and int(result.get("reconciled") or 0) > 0:
        report = build_news_impact_snapshot(
            ticker=args.ticker,
            refresh_ingest=False,
        )
        save_news_impact_snapshot(report, ticker=args.ticker)
    return 0 if result.get("status") == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
