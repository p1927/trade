#!/usr/bin/env python3
"""Ingest verified news for prediction-relevant months without look-ahead bias.

Runs monthly batches (default: 1st-of-month anchors for the first N months of a year).
Each batch ingests headlines for trading days in that calendar month that have factor
history, plus a 7-day lookback ingest around known prediction dates in the month.

Stories with publish_day after the collection day are dropped at ingest time.
Reads for prediction use ``headlines_for_prediction_date`` (publish_day <= prediction_date).
"""

from __future__ import annotations

import argparse
import calendar
import json
import sys
from datetime import date, datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "integrations") not in sys.path:
    sys.path.insert(0, str(ROOT / "integrations"))


def _prediction_dates(ticker: str) -> list[str]:
    dates: set[str] = set()
    try:
        from trade_integrations.dataflows.index_research.prediction_miss_analysis import (
            load_miss_analysis_report,
        )

        report = load_miss_analysis_report(ticker) or {}
        for row in report.get("misses") or []:
            d = str(row.get("prediction_date") or "")[:10]
            if d:
                dates.add(d)
    except Exception:
        pass

    try:
        from trade_integrations.dataflows.index_research.prediction_ledger import load_ledger

        ledger = load_ledger()
        if not ledger.empty:
            for col in ("predicted_at", "date", "prediction_date"):
                if col in ledger.columns:
                    for val in ledger[col].astype(str):
                        d = val[:10]
                        if len(d) >= 10 and d[4] == "-":
                            dates.add(d)
    except Exception:
        pass

    return sorted(dates)


def _trading_days_in_month(
    year: int,
    month: int,
    trading_dates: set[str],
    *,
    cap_today: bool = True,
) -> list[str]:
    today = datetime.now(timezone.utc).date().isoformat()
    last_dom = calendar.monthrange(year, month)[1]
    out: list[str] = []
    for dom in range(1, last_dom + 1):
        day = date(year, month, dom).isoformat()
        if cap_today and day > today:
            continue
        if day in trading_dates:
            out.append(day)
    return out


def _month_anchors(year: int, months: int) -> list[tuple[int, int]]:
    anchors: list[tuple[int, int]] = []
    for month in range(1, min(months, 12) + 1):
        anchors.append((year, month))
    return anchors


def main() -> int:
    parser = argparse.ArgumentParser(description="Monthly prediction-aligned news ingest")
    parser.add_argument("--year", type=int, default=2026)
    parser.add_argument("--months", type=int, default=5, help="First N months of the year")
    parser.add_argument("--ticker", default="NIFTY")
    parser.add_argument("--horizon-days", type=int, default=14)
    parser.add_argument("--lookback-days", type=int, default=7)
    parser.add_argument("--headline-limit", type=int, default=16)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--force-reverify", action="store_true")
    args = parser.parse_args()

    from trade_integrations.context.hub import get_hub_dir
    from trade_integrations.env import load_trade_env
    from trade_integrations.dataflows.index_research.news_impact_engine import (
        ingest_headlines_for_day,
        ingest_lookback_for_prediction_date,
    )
    from trade_integrations.dataflows.index_research.sources.history_loader import (
        load_aligned_factor_history,
    )
    from trade_integrations.dataflows.news_hub_bridge import headlines_for_prediction_date

    load_trade_env()
    frame = load_aligned_factor_history(days=500)
    trading_dates = set(frame["date"].astype(str).str[:10].tolist()) if not frame.empty else set()
    pred_dates = _prediction_dates(args.ticker)

    batches: list[dict[str, object]] = []
    totals = {
        "month_batches": 0,
        "trading_days": 0,
        "prediction_lookbacks": 0,
        "ingested": 0,
        "cache_hits": 0,
        "skipped_lookahead": 0,
        "verified": 0,
        "approved_ui": 0,
    }

    for year, month in _month_anchors(args.year, args.months):
        anchor = date(year, month, 1).isoformat()
        days = _trading_days_in_month(year, month, trading_dates)
        month_preds = [d for d in pred_dates if d.startswith(f"{year:04d}-{month:02d}")]

        batch_stats = {
            "anchor": anchor,
            "trading_days": len(days),
            "prediction_dates": month_preds,
            "day_stats": [],
            "lookback_stats": [],
        }

        if args.dry_run:
            batches.append(batch_stats)
            totals["month_batches"] += 1
            totals["trading_days"] += len(days)
            totals["prediction_lookbacks"] += len(month_preds)
            continue

        for day in days:
            stats = ingest_headlines_for_day(
                ticker=args.ticker,
                day=day,
                horizon_days=args.horizon_days,
                headline_limit=args.headline_limit,
                force_reverify=args.force_reverify,
            )
            batch_stats["day_stats"].append({"day": day, **stats})
            totals["trading_days"] += 1
            for key in ("ingested", "cache_hits", "skipped_lookahead", "verified", "approved_ui"):
                totals[key] += int(stats.get(key) or 0)

        for pred_day in month_preds:
            lb = ingest_lookback_for_prediction_date(
                pred_day,
                ticker=args.ticker,
                lookback_days=args.lookback_days,
                horizon_days=args.horizon_days,
                headline_limit=args.headline_limit,
                force_reverify=args.force_reverify,
            )
            batch_stats["lookback_stats"].append({"prediction_date": pred_day, **lb})
            totals["prediction_lookbacks"] += 1
            for key in ("ingested", "cache_hits", "skipped_lookahead", "verified", "approved_ui"):
                totals[key] += int(lb.get(key) or 0)

            sample = headlines_for_prediction_date(
                pred_day,
                ticker=args.ticker,
                lookback_days=args.lookback_days,
                limit=3,
                ingest_if_missing=False,
            )
            batch_stats.setdefault("prediction_samples", []).append(
                {
                    "prediction_date": pred_day,
                    "headline_count": len(sample),
                    "titles": [str(h.get("title") or "")[:80] for h in sample],
                }
            )

        batches.append(batch_stats)
        totals["month_batches"] += 1

    hub = get_hub_dir()
    print(
        json.dumps(
            {
                "status": "ok",
                "hub": str(hub),
                "prediction_dates": pred_dates,
                **totals,
                "batches": batches,
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
