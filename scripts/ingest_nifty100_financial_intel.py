#!/usr/bin/env python3
"""Ingest Nifty 100 financial intelligence GitHub repo into hub."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "integrations") not in sys.path:
    sys.path.insert(0, str(ROOT / "integrations"))


def main() -> int:
    parser = argparse.ArgumentParser(description="Ingest Nifty 100 financial intelligence into hub")
    parser.add_argument("--force-fetch", action="store_true", help="Re-download Excel workbooks from GitHub")
    parser.add_argument(
        "--panel-only",
        action="store_true",
        help="Write hub _data panel only; skip per-symbol company_research merge",
    )
    args = parser.parse_args()

    from trade_integrations.env import load_trade_env

    load_trade_env()

    from trade_integrations.dataflows.nifty100_financial_intel import ingest_nifty100_financial_intel

    result = ingest_nifty100_financial_intel(
        force_fetch=args.force_fetch,
        merge_company_research=not args.panel_only,
    )
    print(json.dumps(result, indent=2, default=str))
    return 0 if result.get("status") == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
