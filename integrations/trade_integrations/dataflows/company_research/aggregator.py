"""Pipeline orchestrator — stages are added incrementally in later steps."""

from __future__ import annotations

from datetime import datetime, timezone

from .config import get_research_config
from .market import Market, detect_market, normalize_ticker
from .models import CompanyResearchDoc, StageResult
from .sources.calendar_in import fetch_calendar_in
from .sources.identity_in import fetch_identity_in


def _apply_stage(doc: CompanyResearchDoc, result: StageResult) -> None:
    doc.stages.append(result)
    if result.stage == "identity" and result.data:
        doc.identity.update(result.data)
    if result.stage == "calendar" and result.data:
        doc.calendar_events = list(result.data.get("events") or [])


def run_company_research(
    ticker: str,
    *,
    lookahead_days: int | None = None,
) -> CompanyResearchDoc:
    """Run the research pipeline for one ticker."""
    config = get_research_config()
    days = lookahead_days if lookahead_days is not None else config.lookahead_days
    now = datetime.now(timezone.utc)
    normalized = normalize_ticker(ticker, market_default=config.market_default)
    market = detect_market(ticker, market_default=config.market_default)

    doc = CompanyResearchDoc(
        ticker=normalized.display_symbol,
        as_of=now,
        lookahead_days=days,
        market=market.value,
        identity={
            "input_ticker": normalized.input_ticker,
            "base_symbol": normalized.base_symbol,
            "yfinance_symbol": normalized.yfinance_symbol,
            "openalgo_symbol": normalized.openalgo_symbol,
            "openalgo_exchange": normalized.openalgo_exchange,
        },
    )

    market_stage = StageResult(
        stage="market",
        status="ok",
        vendor="trade_integrations.market",
        fetched_at=now,
        data={
            "market": market.value,
            "normalized": {
                "input_ticker": normalized.input_ticker,
                "base_symbol": normalized.base_symbol,
                "yfinance_symbol": normalized.yfinance_symbol,
                "openalgo_symbol": normalized.openalgo_symbol,
                "openalgo_exchange": normalized.openalgo_exchange,
                "display_symbol": normalized.display_symbol,
            },
        },
    )
    _apply_stage(doc, market_stage)

    if market == Market.IN:
        _apply_stage(doc, fetch_identity_in(normalized))
        _apply_stage(doc, fetch_calendar_in(normalized, lookahead_days=days))

    return doc
