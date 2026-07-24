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

    parser.add_argument(
        "--probe-engines",
        action="store_true",
        help="Probe duckduckgo,bing with 3 fixed queries (12s timeout); restart SearXNG if suspended",
    )
    parser.add_argument(
        "--probe-searxng",
        metavar="SOURCE_ID",
        help="Diagnose SearXNG discovery for one source (e.g. motilal_oswal)",
    )
    args = parser.parse_args()

    if args.probe_engines:
        import json as _json

        from trade_integrations.dataflows import searxng_finance
        from trade_integrations.dataflows.searxng_finance import search_finance_one

        engines = ["duckduckgo", "bing"]
        queries = [
            "Nifty 50 target outlook",
            "Nifty 50 forecast today",
            "India stock market Nifty prediction",
        ]
        prev_timeout = searxng_finance.REQUEST_TIMEOUT
        searxng_finance.REQUEST_TIMEOUT = 12
        report: dict = {
            "engines": engines,
            "queries": queries,
            "timeout_sec": 12,
            "cleanup": (
                "If unresponsive_engines is non-empty, run: trade restart (or heal SearXNG) "
                "and verify healthz before a full refresh."
            ),
            "results": [],
        }
        try:
            for query in queries:
                qentry = {"query": query, "engines": {}}
                for engine in engines:
                    rows, failed, raw = search_finance_one(
                        query,
                        engine=engine,
                        category="news",
                        limit=5,
                        time_range="day",
                    )
                    qentry["engines"][engine] = {
                        "engine_failed": failed,
                        "raw_count": raw,
                        "accepted_count": len(rows),
                        "sample_urls": [str(r.get("url") or "")[:120] for r in rows[:3]],
                    }
                report["results"].append(qentry)
        finally:
            searxng_finance.REQUEST_TIMEOUT = prev_timeout
        print(_json.dumps(report, indent=2))
        return 0


    if args.probe_searxng:
        from trade_integrations.dataflows.index_research.external_predictions.fetcher import (
            probe_searxng_for_source,
        )
        from trade_integrations.dataflows.index_research.external_predictions.source_registry import (
            get_source,
        )

        source = get_source(args.probe_searxng.strip().lower())
        if source is None:
            print(json.dumps({"error": f"Unknown source {args.probe_searxng!r}"}, indent=2))
            return 1
        report = probe_searxng_for_source(source, horizon_days=args.horizon)
        print(json.dumps(report, indent=2))
        return 0

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
