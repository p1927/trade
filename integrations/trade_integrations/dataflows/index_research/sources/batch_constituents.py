"""Batch company research for Nifty 50 constituents."""

from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta
from typing import Any, Callable

ProgressCallback = Callable[[str, int, int], None]

from trade_integrations.context.hub import (
    is_cache_fresh,
    load_company_research_json,
    save_company_research,
)
from trade_integrations.dataflows.company_research.aggregator import run_company_research
from trade_integrations.dataflows.company_research.fetch_policy import set_nifty50_batch
from trade_integrations.dataflows.company_research.models import CompanyResearchDoc
from trade_integrations.dataflows.index_research.constituents import load_nifty50_constituents
from trade_integrations.dataflows.index_research.constituent_factors import build_constituent_factors
from trade_integrations.dataflows.index_research.constituent_news_ingest import (
    maybe_ingest_constituent_news,
)
from trade_integrations.dataflows.index_research.models import ConstituentRow, ConstituentSignal

_MAX_WORKERS_ENV = "INDEX_RESEARCH_MAX_WORKERS"


def _max_workers(explicit: int | None) -> int:
    if explicit is not None:
        return max(1, explicit)
    try:
        return max(1, int(os.getenv(_MAX_WORKERS_ENV, "4")))
    except ValueError:
        return 4


def _parse_event_date(value: Any) -> date | None:
    if value is None:
        return None
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        return value.date()
    text = str(value).strip()[:10]
    if not text:
        return None
    for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%d-%b-%Y", "%d/%m/%Y"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def _upcoming_events(
    events: list[dict[str, Any]],
    *,
    lookahead_days: int,
) -> list[dict[str, Any]]:
    """Keep calendar rows with dates from today through the lookahead window."""
    today = date.today()
    end = today + timedelta(days=max(lookahead_days, 1))
    upcoming: list[dict[str, Any]] = []
    for event in events:
        event_date = _parse_event_date(event.get("date"))
        if event_date is None:
            continue
        if today <= event_date <= end:
            upcoming.append(event)
    upcoming.sort(key=lambda row: row.get("date") or "")
    return upcoming


def _sentiment_score(sentiment: dict[str, Any]) -> float | None:
    raw = sentiment.get("score")
    if raw is not None:
        try:
            return float(raw)
        except (TypeError, ValueError):
            pass

    scores = sentiment.get("scores") or []
    if scores:
        total = 0.0
        count = 0
        for row in scores:
            if not isinstance(row, dict):
                continue
            label = str(row.get("label") or "neutral").lower()
            conf = float(row.get("score") or 0.5)
            if label == "positive":
                total += conf
            elif label == "negative":
                total -= conf
            count += 1
        if count:
            return round(total / count, 4)

    summary = sentiment.get("summary") or {}
    if isinstance(summary, dict):
        pos = float(summary.get("positive_pct") or 0.0)
        neg = float(summary.get("negative_pct") or 0.0)
        if pos or neg:
            return round((pos - neg) / 100.0, 4)

    return None


def _build_signal(
    row: ConstituentRow,
    doc: CompanyResearchDoc,
    *,
    lookahead_days: int,
) -> ConstituentSignal:
    events = _upcoming_events(list(doc.calendar_events or []), lookahead_days=lookahead_days)
    sentiment = _sentiment_score(doc.sentiment or {})
    factors = build_constituent_factors(
        doc,
        sector=row.sector,
        upcoming_events=events,
        sentiment_score=sentiment,
    )
    return ConstituentSignal(
        symbol=row.symbol,
        weight=row.weight,
        sector=row.sector,
        events=events,
        factors=factors,
        sentiment_score=sentiment,
        contribution_to_index_pct=None,
    )


def _research_one(
    symbol: str,
    *,
    lookahead_days: int,
    refresh: bool,
) -> CompanyResearchDoc:
    if not refresh and is_cache_fresh(symbol):
        doc = load_company_research_json(symbol)
        if doc is not None:
            return doc
    set_nifty50_batch(True)
    try:
        doc = run_company_research(
            symbol,
            lookahead_days=lookahead_days,
            include_macro=False,
        )
    finally:
        set_nifty50_batch(False)
    save_company_research(doc)
    if refresh:
        maybe_ingest_constituent_news(doc, symbol=symbol, refresh=True)
    return doc


def batch_constituent_research(
    *,
    max_workers: int | None = None,
    lookahead_days: int = 14,
    refresh: bool = False,
    on_progress: ProgressCallback | None = None,
) -> list[ConstituentSignal]:
    """Run or load company research for each Nifty 50 constituent."""
    constituents = load_nifty50_constituents()
    if not constituents:
        return []

    workers = _max_workers(max_workers)
    by_symbol = {row.symbol: row for row in constituents}
    docs: dict[str, CompanyResearchDoc] = {}

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {
            pool.submit(
                _research_one,
                row.symbol,
                lookahead_days=lookahead_days,
                refresh=refresh,
            ): row.symbol
            for row in constituents
        }
        total = len(futures)
        done = 0
        for future in as_completed(futures):
            symbol = futures[future]
            docs[symbol] = future.result()
            done += 1
            if on_progress is not None:
                on_progress(symbol, done, total)

    signals = [
        _build_signal(by_symbol[symbol], docs[symbol], lookahead_days=lookahead_days)
        for symbol in docs
    ]
    signals.sort(key=lambda signal: signal.weight, reverse=True)
    return signals
