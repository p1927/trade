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
from trade_integrations.dataflows.company_research.sources.calendar_us import fetch_calendar_us
from trade_integrations.dataflows.company_research.sources.corp_events import fetch_corp_events
from trade_integrations.dataflows.company_research.sources.earnings_signal import fetch_earnings_signal
from trade_integrations.dataflows.company_research.sources.filings_in import fetch_filings_in
from trade_integrations.dataflows.company_research.sources.filings_us import fetch_filings_us
from trade_integrations.dataflows.company_research.sources.fundamentals_in import fetch_fundamentals_in
from trade_integrations.dataflows.company_research.sources.fundamentals_us import fetch_fundamentals_us
from trade_integrations.dataflows.company_research.sources.identity_in import fetch_identity_in
from trade_integrations.dataflows.company_research.sources.identity_us import fetch_identity_us
from trade_integrations.dataflows.company_research.sources.macro_in import fetch_macro_in
from trade_integrations.dataflows.company_research.sources.macro_us import fetch_macro_us
from trade_integrations.dataflows.company_research.sources.news import fetch_news
from trade_integrations.dataflows.company_research.sources.peers_in import fetch_peers_in
from trade_integrations.dataflows.company_research.sources.peers_us import fetch_peers_us
from trade_integrations.dataflows.company_research.sources.sentiment import fetch_sentiment


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


def _norm(args, market: Market | None = None):
    hint = market or (Market(args.market) if getattr(args, "market", None) else None)
    return normalize_ticker(args.ticker, market_hint=hint) if hint else normalize_ticker(args.ticker)


_STAGE_RUNNERS = {
    "market": lambda args: _stage_market(args.ticker),
    "identity_in": lambda args: fetch_identity_in(_norm(args, Market.IN)),
    "identity_us": lambda args: fetch_identity_us(_norm(args, Market.US)),
    "calendar_in": lambda args: fetch_calendar_in(
        _norm(args, Market.IN), lookahead_days=args.days, lookback_days=args.lookback
    ),
    "calendar_us": lambda args: fetch_calendar_us(
        _norm(args, Market.US), lookahead_days=args.days, lookback_days=args.lookback
    ),
    "peers_in": lambda args: fetch_peers_in(_norm(args, Market.IN)),
    "peers_us": lambda args: fetch_peers_us(_norm(args, Market.US)),
    "fundamentals_in": lambda args: fetch_fundamentals_in(_norm(args, Market.IN)),
    "fundamentals_us": lambda args: fetch_fundamentals_us(_norm(args, Market.US)),
    "filings_in": lambda args: fetch_filings_in(
        _norm(args, Market.IN), lookback_days=args.lookback
    ),
    "filings_us": lambda args: fetch_filings_us(_norm(args, Market.US)),
    "news": lambda args: fetch_news(_norm(args, Market.IN), lookback_days=args.days),
    "sentiment": lambda args: fetch_sentiment(
        headlines=[],
        text=args.text or "Apple beats Q1 earnings estimates by 12%",
    ),
    "macro_in": lambda args: fetch_macro_in(),
    "macro_us": lambda args: fetch_macro_us(),
    "earnings_signal": lambda args: fetch_earnings_signal(_norm(args, Market.US), market=Market.US),
    "corp_events": lambda args: fetch_corp_events(_norm(args, Market.US), market=Market.US),
    "pipeline": lambda args: run_company_research(args.ticker, lookahead_days=args.days).stages[-1],
}


def main() -> int:
    parser = argparse.ArgumentParser(description="Smoke-test company research stages")
    parser.add_argument("--stage", required=True, help="Stage name (market, pipeline, …)")
    parser.add_argument("--ticker", default="RELIANCE")
    parser.add_argument("--days", type=int, default=14)
    parser.add_argument("--lookback", type=int, default=None, help="Calendar lookback days")
    parser.add_argument("--market", choices=["IN", "US"], default=None)
    parser.add_argument("--text", default=None, help="Free text for sentiment stages")
    args = parser.parse_args()
    if args.lookback is None:
        from trade_integrations.dataflows.company_research.config import get_research_config

        args.lookback = get_research_config().calendar_lookback_days

    if args.market and args.stage == "market":
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
