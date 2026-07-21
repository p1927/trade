#!/usr/bin/env python3
"""Archive company_research snapshots for hybrid walk-forward backtest."""

from __future__ import annotations

import argparse
import json
from datetime import date, timedelta
from pathlib import Path

from trade_integrations.context.hub import get_hub_dir, load_company_research_json
from trade_integrations.dataflows.index_research.constituents import load_nifty50_constituents


def _history_path(symbol: str, day: str) -> Path:
    return get_hub_dir() / symbol.strip().upper() / "company_research" / "history" / f"{day[:10]}.json"


def archive_constituent_history(*, days: int = 180, dry_run: bool = False) -> dict[str, int]:
    end = date.today()
    start = end - timedelta(days=max(days, 1))
    constituents = load_nifty50_constituents()
    archived = 0
    skipped = 0
    for offset in range((end - start).days + 1):
        day = (start + timedelta(days=offset)).isoformat()
        for row in constituents:
            latest = load_company_research_json(row.symbol)
            if not latest:
                skipped += 1
                continue
            path = _history_path(row.symbol, day)
            if path.is_file():
                skipped += 1
                continue
            payload = {
                "symbol": row.symbol,
                "as_of": day,
                "sentiment": latest.get("sentiment") or {},
                "return_7d_pct": latest.get("return_7d_pct") or latest.get("momentum_7d_pct"),
                "source": "archive_constituent_history",
            }
            if dry_run:
                archived += 1
                continue
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
            archived += 1
    return {"archived": archived, "skipped": skipped}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--days", type=int, default=180)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    result = archive_constituent_history(days=args.days, dry_run=args.dry_run)
    print(result)


if __name__ == "__main__":
    main()
