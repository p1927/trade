#!/usr/bin/env python3
"""Refresh third-party NIFTY predictions from watchlisted sources."""

from __future__ import annotations

import argparse
import json
import sys


def main() -> int:
    parser = argparse.ArgumentParser(description="Refresh external NIFTY predictions")
    parser.add_argument("--symbol", default="NIFTY")
    parser.add_argument("--horizon", type=int, default=30)
    parser.add_argument("--discover", action="store_true", help="Run source discovery only")
    args = parser.parse_args()

    if args.discover:
        from trade_integrations.dataflows.index_research.external_predictions.discover import (
            discover_external_sources,
        )

        rows = discover_external_sources()
        print(json.dumps({"candidates": rows}, indent=2))
        return 0

    from trade_integrations.dataflows.index_research.external_predictions.refresh import (
        refresh_all_external_predictions,
    )

    snapshot = refresh_all_external_predictions(
        symbol=args.symbol,
        horizon_days=args.horizon,
    )
    ok_count = sum(1 for p in snapshot.predictions if p.fetch_status == "ok")
    print(
        json.dumps(
            {
                "status": "ok",
                "fetched_at": snapshot.fetched_at,
                "horizon_days": snapshot.horizon_days,
                "sources": len(snapshot.sources),
                "ok_predictions": ok_count,
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
