"""SearXNG source adapter for the news aggregator."""

from __future__ import annotations

import logging

from tradingagents.dataflows.config import get_config
from trade_integrations.dataflows.searxng_news import _parse_pub_date, _search

from ..config import FETCH_MULTIPLIER

from ..models import NewsArticle

logger = logging.getLogger(__name__)
VENDOR = "searxng"


def fetch_ticker_articles(
    ticker: str,
    *,
    start_date: str = "",
    end_date: str = "",
    limit: int,
) -> list[NewsArticle]:
    del start_date, end_date
    results = _search(f"{ticker} stock news", limit * FETCH_MULTIPLIER)
    return _to_articles(results)


def fetch_global_articles(
    *,
    curr_date: str = "",
    look_back_days: int = 7,
    limit: int,
) -> list[NewsArticle]:
    del curr_date, look_back_days
    config = get_config()
    articles: list[NewsArticle] = []
    seen_titles: set[str] = set()

    for query in config["global_news_queries"]:
        for result in _search(query, limit):
            title = (result.get("title") or "").strip()
            if not title or title in seen_titles:
                continue
            seen_titles.add(title)
            articles.append(_to_article(result))
        if len(articles) >= limit:
            break

    return articles[: limit * FETCH_MULTIPLIER]


def _to_articles(results: list[dict]) -> list[NewsArticle]:
    return [_to_article(result) for result in results if (result.get("title") or "").strip()]


def _to_article(result: dict) -> NewsArticle:
    engines = result.get("engines") or []
    source = ", ".join(engines) if engines else "SearXNG"
    return NewsArticle(
        title=(result.get("title") or "").strip(),
        summary=(result.get("content") or "").strip(),
        link=(result.get("url") or "").strip(),
        source=source,
        vendor=VENDOR,
        pub_date=_parse_pub_date(result),
    )
