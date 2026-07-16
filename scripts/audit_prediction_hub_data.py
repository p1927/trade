#!/usr/bin/env python3
"""Audit hub data completeness for prediction miss RCA."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tradingagents"))

import trade_integrations  # noqa: F401

from trade_integrations.dataflows.index_research.hub_data_audit import run_and_save_data_audit


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit hub data for prediction RCA")
    parser.add_argument("--days", type=int, default=365, help="History window in calendar days")
    parser.add_argument("--horizon-days", type=int, default=14, help="Forecast horizon")
    parser.add_argument("--ticker", type=str, default="NIFTY")
    args = parser.parse_args()

    report = run_and_save_data_audit(
        days=args.days,
        horizon_days=args.horizon_days,
        ticker=args.ticker,
    )
    print(json.dumps(
        {
            "status": report.get("status"),
            "trading_rows": report.get("trading_rows"),
            "blocking_gaps": report.get("blocking_gaps"),
            "recommendations": report.get("recommendations"),
        },
        indent=2,
    ))
    if report.get("status") != "ok":
        return 1
    print(f"\nSaved: reports/hub/{args.ticker.upper()}/index_research/data_audit_latest.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
