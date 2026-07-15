"""yfinance source adapter for the news aggregator."""

from __future__ import annotations

import logging

import yfinance as yf

from tradingagents.dataflows.config import get_config
from tradingagents.dataflows.stockstats_utils import yf_retry
from tradingagents.dataflows.symbol_utils import normalize_symbol
from tradingagents.dataflows.yfinance_news import _extract_article_data

from ..config import FETCH_MULTIPLIER
from ..models import NewsArticle

logger = logging.getLogger(__name__)
VENDOR = "yfinance"


def fetch_ticker_articles(
    ticker: str,
    *,
    start_date: str = "",
    end_date: str = "",
    limit: int,
) -> list[NewsArticle]:
    del start_date, end_date
    canonical = normalize_symbol(ticker)
    stock = yf.Ticker(canonical)
    news = yf_retry(lambda: stock.get_news(count=limit * FETCH_MULTIPLIER))
    return _to_articles(news or [])


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
        search = yf_retry(
            lambda q=query: yf.Search(
                query=q,
                news_count=limit,
                enable_fuzzy_query=True,
            )
        )
        for raw in search.news or []:
            data = _extract_article_data(raw)
            title = data["title"]
            if not title or title in seen_titles:
                continue
            seen_titles.add(title)
            articles.append(
                NewsArticle(
                    title=title,
                    summary=data["summary"],
                    link=data["link"],
                    source=data["publisher"],
                    vendor=VENDOR,
                    pub_date=data["pub_date"],
                )
            )
        if len(articles) >= limit:
            break

    return articles[: limit * FETCH_MULTIPLIER]


def _to_articles(raw_articles: list[dict]) -> list[NewsArticle]:
    articles: list[NewsArticle] = []
    for raw in raw_articles:
        data = _extract_article_data(raw)
        title = data["title"]
        if not title:
            continue
        articles.append(
            NewsArticle(
                title=title,
                summary=data["summary"],
                link=data["link"],
                source=data["publisher"],
                vendor=VENDOR,
                pub_date=data["pub_date"],
            )
        )
    return articles
