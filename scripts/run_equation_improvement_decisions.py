#!/usr/bin/env python3
"""Generate equation improvement decisions markdown from hub artifacts."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tradingagents"))

import trade_integrations  # noqa: F401

from trade_integrations.dataflows.index_research.equation_improvement_decisions import run_and_save_decisions


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate equation improvement decisions")
    parser.add_argument("--ticker", type=str, default="NIFTY")
    args = parser.parse_args()

    result = run_and_save_decisions(ticker=args.ticker)
    print(json.dumps(result, indent=2))
    return 0 if result.get("status") == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
