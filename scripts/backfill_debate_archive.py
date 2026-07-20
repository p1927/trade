#!/usr/bin/env python3
"""Backfill dated agent debate archives for walk-forward backtest eligibility."""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "integrations") not in sys.path:
    sys.path.insert(0, str(ROOT / "integrations"))


def backfill_debate_archive(
    *,
    ticker: str = "NIFTY",
    days: int = 730,
    sample_every: int = 7,
    max_runs: int | None = None,
    sleep_seconds: float = 1.0,
    dry_run: bool = False,
) -> dict[str, int | str]:
    """Weekly sample over walk-forward eval dates; invokes real TradingAgents debate."""
    from trade_integrations.context.hub import count_agent_debate_history, get_hub_dir
    from trade_integrations.dataflows.index_research.sources.history_loader import load_nifty_history

    ticker = ticker.strip().upper()
    nifty = load_nifty_history(days=days)
    if nifty.empty:
        return {"status": "error", "reason": "no_nifty_history"}

    trading_days = nifty["date"].astype(str).tolist()[:: max(1, sample_every)]
    history_dir = get_hub_dir() / ticker / "agent_debate" / "history"
    existing = {p.stem[:10] for p in history_dir.glob("*.json")} if history_dir.is_dir() else set()
    pending = [d for d in trading_days if d[:10] not in existing]
    if max_runs is not None:
        pending = pending[: max(0, max_runs)]

    if dry_run:
        return {
            "status": "dry_run",
            "ticker": ticker,
            "sample_days": len(trading_days),
            "pending": len(pending),
            "existing_history": len(existing),
            "debate_history_count": count_agent_debate_history(ticker),
        }

    from trade_integrations.bridge.agent_debate import debate_eligible_for_ticker, run_agent_debate

    eligible, block_reason = debate_eligible_for_ticker(ticker)
    if not eligible:
        return {"status": "blocked", "reason": block_reason or "not_eligible", "ticker": ticker}

    written = 0
    skipped = 0
    errors = 0
    for day in pending:
        try:
            payload = run_agent_debate(ticker, trade_date=day[:10])
            if payload:
                written += 1
            else:
                skipped += 1
        except Exception as exc:
            errors += 1
            print(f"debate failed {day}: {exc}", file=sys.stderr)
        time.sleep(sleep_seconds)

    return {
        "status": "ok",
        "ticker": ticker,
        "written": written,
        "skipped": skipped,
        "errors": errors,
        "debate_history_count": count_agent_debate_history(ticker),
        "sample_days": len(trading_days),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Backfill agent debate history archives")
    parser.add_argument("--ticker", default="NIFTY")
    parser.add_argument("--days", type=int, default=730)
    parser.add_argument("--sample-every", type=int, default=7, help="Trading-day stride (7 = weekly)")
    parser.add_argument("--max-runs", type=int, default=None, help="Cap debate invocations per run")
    parser.add_argument("--sleep", type=float, default=1.0)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    from trade_integrations.env import load_trade_env

    load_trade_env()
    result = backfill_debate_archive(
        ticker=args.ticker,
        days=args.days,
        sample_every=args.sample_every,
        max_runs=args.max_runs,
        sleep_seconds=args.sleep,
        dry_run=args.dry_run,
    )
    print(json.dumps(result, indent=2, default=str))
    return 0 if result.get("status") in {"ok", "dry_run", "blocked"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
