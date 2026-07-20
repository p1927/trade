#!/usr/bin/env python3
"""Materialize wide NIFTY factor panel from cold-tier history datasets."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "integrations") not in sys.path:
    sys.path.insert(0, str(ROOT / "integrations"))


def main() -> int:
    parser = argparse.ArgumentParser(description="Build materialized history panel")
    parser.add_argument("--start", default="2006-01-01")
    parser.add_argument("--end", default=None)
    parser.add_argument("--panel", default="NIFTY_2006_present")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    from trade_integrations.env import load_trade_env
    from trade_integrations.dataflows.index_research.history_panel import materialize_panel

    load_trade_env()
    result = materialize_panel(
        start=args.start,
        end=args.end,
        panel_name=args.panel,
        dry_run=args.dry_run,
    )
    print(json.dumps(result, indent=2))
    return 0 if result.get("status") in {"ok", "dry_run"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
