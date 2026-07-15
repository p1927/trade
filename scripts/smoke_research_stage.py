#!/usr/bin/env python3
"""Smoke-test one company research pipeline stage."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tradingagents"))

import trade_integrations  # noqa: F401 — activate integrations

from trade_integrations.dataflows.company_research.aggregator import run_company_research
from trade_integrations.dataflows.company_research.format import format_research_report
from trade_integrations.dataflows.company_research.market import Market, detect_market, normalize_ticker
from trade_integrations.dataflows.company_research.models import StageResult
from trade_integrations.dataflows.company_research.sources.calendar_in import fetch_calendar_in
from trade_integrations.dataflows.company_research.sources.identity_in import fetch_identity_in


def _stage_market(ticker: str) -> StageResult:
    from datetime import datetime, timezone

    normalized = normalize_ticker(ticker)
    market = detect_market(ticker)
    return StageResult(
        stage="market",
        status="ok",
        vendor="trade_integrations.market",
        fetched_at=datetime.now(timezone.utc),
        data={
            "market": market.value,
            "normalized": asdict(normalized),
        },
    )


_STAGE_RUNNERS = {
    "market": lambda args: _stage_market(args.ticker),
    "identity_in": lambda args: fetch_identity_in(
        normalize_ticker(args.ticker, market_hint=Market.IN)
    ),
    "calendar_in": lambda args: fetch_calendar_in(
        normalize_ticker(args.ticker, market_hint=Market.IN),
        lookahead_days=args.days,
    ),
    "pipeline": lambda args: run_company_research(args.ticker, lookahead_days=args.days).stages[-1],
}


def main() -> int:
    parser = argparse.ArgumentParser(description="Smoke-test company research stages")
    parser.add_argument("--stage", required=True, help="Stage name (market, pipeline, …)")
    parser.add_argument("--ticker", default="RELIANCE")
    parser.add_argument("--days", type=int, default=14)
    parser.add_argument("--market", choices=["IN", "US"], default=None)
    parser.add_argument("--text", default=None, help="Free text for sentiment stages")
    args = parser.parse_args()

    if args.market:
        hint = Market(args.market)
        normalized = normalize_ticker(args.ticker, market_hint=hint)
        print(json.dumps({"normalized": asdict(normalized)}, default=str, indent=2))
        return 0

    runner = _STAGE_RUNNERS.get(args.stage)
    if runner is None:
        print(f"Unknown stage {args.stage!r}. Available: {', '.join(sorted(_STAGE_RUNNERS))}", file=sys.stderr)
        return 1

    if args.stage == "pipeline":
        doc = run_company_research(args.ticker, lookahead_days=args.days)
        print(format_research_report(doc))
        result = doc.stages[-1] if doc.stages else None
    else:
        result = runner(args)

    if result is None:
        print("No stage result", file=sys.stderr)
        return 1

    payload = asdict(result)
    print(json.dumps(payload, default=str, indent=2))

    if result.status in ("ok", "partial") and result.data:
        return 0
    if result.status == "skipped":
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
