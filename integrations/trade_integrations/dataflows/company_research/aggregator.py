"""Pipeline orchestrator — India and US enrichment stages."""

from __future__ import annotations

from datetime import datetime, timezone

from .config import get_research_config
from .market import Market, detect_market, normalize_ticker
from .models import CompanyResearchDoc, StageResult
from .sources.calendar_in import fetch_calendar_in
from .sources.calendar_us import fetch_calendar_us
from .sources.corp_events import fetch_corp_events
from .sources.earnings_signal import fetch_earnings_signal
from .sources.filings_in import fetch_filings_in
from .sources.filings_us import fetch_filings_us
from .sources.fundamentals_in import fetch_fundamentals_in
from .sources.fundamentals_us import fetch_fundamentals_us
from .sources.identity_in import fetch_identity_in
from .sources.identity_us import fetch_identity_us
from .sources.macro_in import fetch_macro_in
from .sources.macro_us import fetch_macro_us
from .sources.news import fetch_news
from .sources.peers_in import fetch_peers_in
from .sources.peers_us import fetch_peers_us
from .sources.sentiment import fetch_sentiment


def _apply_stage(doc: CompanyResearchDoc, result: StageResult) -> None:
    doc.stages.append(result)
    if result.stage == "identity" and result.data:
        doc.identity.update(result.data)
    if result.stage == "calendar" and result.data:
        doc.calendar_events = list(result.data.get("events") or [])
    if result.stage == "peers" and result.data:
        doc.peers = list(result.data.get("peers") or [])
    if result.stage == "fundamentals" and result.data:
        doc.fundamentals.update(result.data)
    if result.stage == "filings" and result.data:
        doc.filings.update(result.data)
    if result.stage == "news" and result.data:
        doc.news = dict(result.data)
    if result.stage == "sentiment" and result.data:
        doc.sentiment.update(result.data)
    if result.stage == "corp_events" and result.data:
        doc.corp_events.update(result.data)
    if result.stage == "earnings_signal" and result.data:
        doc.earnings_signal.update(result.data)
    if result.stage == "macro" and result.data:
        doc.macro.update(result.data)


def _headlines_from_news(news: dict) -> list[str]:
    headlines: list[str] = []
    for block in news.get("blocks") or []:
        for row in block.get("headlines") or []:
            title = row.get("title") if isinstance(row, dict) else str(row)
            if title:
                headlines.append(str(title))
    return headlines


def run_company_research(
    ticker: str,
    *,
    lookahead_days: int | None = None,
    include_macro: bool = True,
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
        identity_result = fetch_identity_in(normalized)
        _apply_stage(doc, identity_result)
        industry_hint = str(doc.identity.get("industry") or "")
        _apply_stage(doc, fetch_peers_in(normalized, industry_hint=industry_hint))
        _apply_stage(
            doc,
            fetch_calendar_in(
                normalized,
                lookahead_days=days,
                lookback_days=config.calendar_lookback_days,
            ),
        )
        _apply_stage(doc, fetch_fundamentals_in(normalized))
        _apply_stage(
            doc,
            fetch_filings_in(normalized, lookback_days=config.calendar_lookback_days),
        )
        _apply_stage(
            doc,
            fetch_news(
                normalized,
                peers=doc.peers,
                lookback_days=config.news_lookback_days,
            ),
        )
        from trade_integrations.hub_capture.channel import resolve_registered_entity

        capture_entity = resolve_registered_entity(normalized.base_symbol)
        _apply_stage(
            doc,
            fetch_sentiment(
                headlines=_headlines_from_news(doc.news),
                capture_entity=capture_entity,
            ),
        )
        if include_macro:
            _apply_stage(doc, fetch_macro_in())
    else:
        _apply_stage(doc, fetch_identity_us(normalized))
        _apply_stage(doc, fetch_peers_us(normalized))
        _apply_stage(
            doc,
            fetch_calendar_us(
                normalized,
                lookahead_days=days,
                lookback_days=config.calendar_lookback_days,
            ),
        )
        _apply_stage(doc, fetch_fundamentals_us(normalized))
        _apply_stage(doc, fetch_filings_us(normalized))
        _apply_stage(
            doc,
            fetch_news(
                normalized,
                peers=doc.peers,
                lookback_days=config.news_lookback_days,
            ),
        )
        from trade_integrations.hub_capture.channel import resolve_registered_entity

        capture_entity = resolve_registered_entity(normalized.base_symbol)
        _apply_stage(
            doc,
            fetch_sentiment(
                headlines=_headlines_from_news(doc.news),
                capture_entity=capture_entity,
            ),
        )
        _apply_stage(doc, fetch_earnings_signal(normalized, market=market))
        _apply_stage(doc, fetch_corp_events(normalized, market=market))
        if include_macro:
            _apply_stage(doc, fetch_macro_us())

    return doc


def run_company_research_batch(
    tickers: list[str],
    *,
    lookahead_days: int | None = None,
) -> list[CompanyResearchDoc]:
    """Run the pipeline for multiple tickers (dual-market aware)."""
    docs: list[CompanyResearchDoc] = []
    for ticker in tickers:
        ticker = ticker.strip()
        if not ticker:
            continue
        docs.append(run_company_research(ticker, lookahead_days=lookahead_days, include_macro=False))
    return docs
