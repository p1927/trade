"""Merge news from multiple backends, deduplicate, and format for agents."""

from __future__ import annotations

import logging
from datetime import datetime

from dateutil.relativedelta import relativedelta

from tradingagents.dataflows.config import get_config
from tradingagents.dataflows.errors import VendorNotConfiguredError, VendorRateLimitError
from tradingagents.dataflows.symbol_utils import normalize_symbol
from tradingagents.dataflows.yfinance_news import _in_news_window

from .config import get_aggregator_sources
from .dedup import deduplicate_articles
from .format import format_global_news, format_ticker_news
from .models import NewsArticle
from .select import select_diverse_articles
from .sources import SOURCE_REGISTRY
from .sources.alpha_vantage import is_configured as alpha_vantage_configured

logger = logging.getLogger(__name__)


def _naive_dt(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value.replace(tzinfo=None) if value.tzinfo else value


def _sort_articles(articles: list[NewsArticle]) -> list[NewsArticle]:
    dated = [a for a in articles if a.pub_date is not None]
    undated = [a for a in articles if a.pub_date is None]
    dated.sort(key=lambda a: _naive_dt(a.pub_date), reverse=True)
    return dated + undated


def _filter_window(
    articles: list[NewsArticle],
    *,
    start_dt: datetime,
    end_dt: datetime,
) -> list[NewsArticle]:
    return [
        article
        for article in articles
        if _in_news_window(article.pub_date, start_dt, end_dt)
    ]


def _fetch_from_source(
    source_name: str,
    *,
    kind: str,
    ticker: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    curr_date: str | None = None,
    look_back_days: int | None = None,
    limit: int,
) -> list[NewsArticle]:
    adapter = SOURCE_REGISTRY.get(source_name)
    if adapter is None:
        logger.warning("Unknown news aggregator source %r; skipping.", source_name)
        return []

    if source_name == "alpha_vantage" and not alpha_vantage_configured():
        logger.info("Alpha Vantage not configured; skipping in news aggregator.")
        return []

    try:
        from trade_integrations.dataflows.company_research.fetch_policy import (
            tiered_source_allowed,
        )

        if not tiered_source_allowed(source_name):
            logger.debug("Source %r skipped (Nifty 50 batch — tiered APIs off).", source_name)
            return []
    except ImportError:
        pass

    try:
        if kind == "ticker":
            return adapter.fetch_ticker_articles(
                ticker or "",
                start_date=start_date or "",
                end_date=end_date or "",
                limit=limit,
            )
        return adapter.fetch_global_articles(
            curr_date=curr_date or "",
            look_back_days=look_back_days or 7,
            limit=limit,
        )
    except VendorNotConfiguredError:
        logger.info("Source %r not configured; skipping.", source_name)
        return []
    except VendorRateLimitError as exc:
        logger.warning("Source %r rate-limited; skipping: %s", source_name, exc)
        return []
    except Exception as exc:
        logger.warning("Source %r failed; skipping: %s", source_name, exc)
        return []


def _merge_articles(
    *,
    kind: str,
    sources: list[str],
    start_dt: datetime,
    end_dt: datetime,
    limit: int,
    ticker: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    curr_date: str | None = None,
    look_back_days: int | None = None,
) -> list[NewsArticle]:
    collected: list[NewsArticle] = []
    for source_name in sources:
        collected.extend(
            _fetch_from_source(
                source_name,
                kind=kind,
                ticker=ticker,
                start_date=start_date,
                end_date=end_date,
                curr_date=curr_date,
                look_back_days=look_back_days,
                limit=limit,
            )
        )

    merged = deduplicate_articles(collected)
    filtered = _filter_window(merged, start_dt=start_dt, end_dt=end_dt)
    sorted_articles = _sort_articles(filtered)
    return select_diverse_articles(sorted_articles, limit)


def get_news_aggregated(ticker: str, start_date: str, end_date: str) -> str:
    """Fetch ticker news from all configured sources, dedupe, and format."""
    config = get_config()
    limit = config["news_article_limit"]
    sources = get_aggregator_sources()
    start_dt = datetime.strptime(start_date, "%Y-%m-%d")
    end_dt = datetime.strptime(end_date, "%Y-%m-%d")

    canonical = normalize_symbol(ticker)
    resolved = "" if canonical == ticker else f" (resolved to {canonical})"

    articles = _merge_articles(
        kind="ticker",
        sources=sources,
        ticker=canonical,
        start_date=start_date,
        end_date=end_date,
        start_dt=start_dt,
        end_dt=end_dt,
        limit=limit,
    )
    try:
        from trade_integrations.dataflows.news_hub_bridge import (
            enrich_articles_with_hub_tags,
            ingest_news_articles,
        )

        ingest_news_articles(articles, ticker=canonical, collection_day=end_date)
        articles = enrich_articles_with_hub_tags(articles, ticker=canonical)
    except Exception as exc:
        logger.warning("hub bridge ticker news ingest failed: %s", exc, exc_info=True)
    return format_ticker_news(
        articles,
        ticker=ticker,
        start_date=start_date,
        end_date=end_date,
        resolved_suffix=resolved,
    )


def get_global_news_aggregated(
    curr_date: str,
    look_back_days: int | None = None,
    limit: int | None = None,
) -> str:
    """Fetch macro news from all configured sources, dedupe, and format."""
    config = get_config()
    if look_back_days is None:
        look_back_days = config["global_news_lookback_days"]
    if limit is None:
        limit = config["global_news_article_limit"]

    curr_dt = datetime.strptime(curr_date, "%Y-%m-%d")
    start_dt = curr_dt - relativedelta(days=look_back_days)
    start_date = start_dt.strftime("%Y-%m-%d")

    articles = _merge_articles(
        kind="global",
        sources=get_aggregator_sources(),
        curr_date=curr_date,
        look_back_days=look_back_days,
        start_dt=start_dt,
        end_dt=curr_dt,
        limit=limit,
    )
    try:
        from trade_integrations.dataflows.news_hub_bridge import (
            enrich_articles_with_hub_tags,
            ingest_news_articles,
        )

        ingest_news_articles(articles, ticker="NIFTY", kind="global", collection_day=curr_date)
        articles = enrich_articles_with_hub_tags(articles, ticker="NIFTY")
    except Exception as exc:
        logger.warning("hub bridge global news ingest failed: %s", exc, exc_info=True)
    return format_global_news(articles, start_date=start_date, end_date=curr_date)
