#!/usr/bin/env python3
"""Backfill cold-tier history used by NIFTY prediction tracks (2006+).

Loads only datasets wired into MACRO_FACTOR_KEYS, news overlay, and walk-forward
backtest — not unused global indices, bhavcopy, or broad US macro panels.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "integrations") not in sys.path:
    sys.path.insert(0, str(ROOT / "integrations"))


def main() -> int:
    parser = argparse.ArgumentParser(description="Backfill prediction-only history (2006+)")
    parser.add_argument("--start", default="2006-01-01")
    parser.add_argument("--end", default=None)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--skip-macro", action="store_true")
    parser.add_argument("--skip-flows", action="store_true")
    parser.add_argument("--skip-news", action="store_true")
    parser.add_argument("--skip-panel", action="store_true")
    parser.add_argument("--skip-factor-store", action="store_true", help="Skip daily factor parquet snapshots")
    parser.add_argument("--offline-flows", action="store_true", help="Use cached/repo flows only")
    parser.add_argument("--no-gdelt", action="store_true", help="News from curated calendar only")
    args = parser.parse_args()

    from trade_integrations.env import load_trade_env

    load_trade_env()

    results: dict[str, object] = {"start": args.start, "end": args.end, "dry_run": args.dry_run}

    if not args.skip_macro:
        from trade_integrations.dataflows.index_research.sources.historical_macro import (
            backfill_macro_history,
        )

        results["macro"] = backfill_macro_history(
            start=args.start, end=args.end, dry_run=args.dry_run
        )

    if not args.skip_flows:
        from trade_integrations.dataflows.index_research.sources.historical_flows import (
            backfill_flow_history,
        )

        results["flows"] = backfill_flow_history(
            start=args.start,
            end=args.end,
            allow_live_fetch=not args.offline_flows,
            dry_run=args.dry_run,
        )

    if not args.skip_news:
        from trade_integrations.dataflows.index_research.sources.historical_news import (
            backfill_news_history,
        )

        results["news"] = backfill_news_history(
            start=args.start,
            end=args.end,
            use_gdelt=not args.no_gdelt,
            gdelt_max_days=None if not args.no_gdelt else 0,
            dry_run=args.dry_run,
        )

    if not args.skip_panel and not args.dry_run:
        from trade_integrations.dataflows.index_research.history_panel import materialize_panel
        from trade_integrations.dataflows.index_research.prediction_data_requirements import (
            audit_prediction_panel_coverage,
            load_panel_for_audit,
        )

        panel_result = materialize_panel(start=args.start, end=args.end)
        results["panel"] = panel_result

        frame = load_panel_for_audit()
        results["coverage"] = audit_prediction_panel_coverage(frame)

    if not args.skip_factor_store and not args.dry_run:
        macro_ok = results.get("macro", {}).get("status") == "ok"  # type: ignore[union-attr]
        if macro_ok or args.skip_macro:
            from trade_integrations.dataflows.index_research.factor_backfill import (
                backfill_factor_history,
            )

            results["factor_store"] = backfill_factor_history(days=5000, start=args.start)

    print(json.dumps(results, indent=2, default=str))
    panel_status = (results.get("panel") or {}).get("status") if isinstance(results.get("panel"), dict) else None
    if args.dry_run:
        return 0
    if panel_status == "ok" or args.skip_panel:
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
