#!/usr/bin/env python3
"""Backfill factor history and constituent news for prediction miss RCA."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tradingagents"))

import trade_integrations  # noqa: F401


def main() -> int:
    parser = argparse.ArgumentParser(description="Backfill hub data for prediction RCA")
    parser.add_argument("--days", type=int, default=365)
    parser.add_argument("--horizon-days", type=int, default=14)
    parser.add_argument("--skip-factors", action="store_true")
    parser.add_argument("--skip-news", action="store_true")
    parser.add_argument("--news-sleep", type=float, default=0.35)
    args = parser.parse_args()

    results: dict[str, object] = {"status": "ok"}

    if not args.skip_factors:
        from trade_integrations.dataflows.index_research.factor_backfill_enrichment import (
            enrich_factor_history,
        )

        results["factor_enrichment"] = enrich_factor_history(days=args.days)

    if not args.skip_news:
        from trade_integrations.dataflows.index_research.backtest_runner import load_backtest_report
        from trade_integrations.dataflows.index_research.company_news_backfill import (
            backfill_constituent_news_day,
        )
        from trade_integrations.dataflows.index_research.constituents import load_nifty50_constituents
        from trade_integrations.dataflows.index_research.prediction_miss_analysis import (
            resolve_maturity_date,
        )
        from trade_integrations.dataflows.index_research.sources.history_loader import (
            load_aligned_factor_history,
        )

        frame = load_aligned_factor_history(days=args.days)
        trading = frame["date"].astype(str).str[:10].tolist() if not frame.empty else []
        backtest = load_backtest_report("NIFTY") or {}
        eval_dates = {
            str(row.get("date") or "")[:10]
            for row in (backtest.get("daily_evaluations") or [])
            if row.get("direction_correct") is False
        }
        for pred_day in sorted(eval_dates):
            maturity = resolve_maturity_date(pred_day, args.horizon_days, trading)
            if maturity:
                eval_dates.add(maturity)

        constituents = [row.symbol for row in load_nifty50_constituents()]
        written = 0
        skipped = 0
        for sym in constituents:
            for day in sorted(eval_dates):
                if backfill_constituent_news_day(sym, day):
                    written += 1
                else:
                    skipped += 1
        results["news_backfill"] = {
            "eval_dates": len(eval_dates),
            "symbols": len(constituents),
            "written": written,
            "skipped": skipped,
        }

    print(json.dumps(results, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
