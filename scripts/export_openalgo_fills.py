#!/usr/bin/env python3
"""Export OpenAlgo sandbox fills into reports/hub/_data/trades/fills.parquet."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "integrations") not in sys.path:
    sys.path.insert(0, str(ROOT / "integrations"))

from trade_integrations.env import load_trade_env
from trade_integrations.hub_storage.openalgo_fills_export import export_openalgo_fills


def main() -> int:
    parser = argparse.ArgumentParser(description="Export OpenAlgo sandbox fills to hub parquet")
    parser.add_argument("--dry-run", action="store_true", help="Report counts without writing")
    parser.add_argument("--json", action="store_true", help="Print summary JSON")
    args = parser.parse_args()

    load_trade_env()
    summary = export_openalgo_fills(dry_run=args.dry_run)
    if args.json:
        print(json.dumps(summary, indent=2, default=str))
    else:
        print(
            f"{summary.get('status')}: new={summary.get('new_rows')} "
            f"total={summary.get('total_rows')} db={summary.get('sandbox_db')}"
        )
    return 0 if summary.get("status") in {"ok", "skipped"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
