"""Alpha Vantage source adapter for the news aggregator."""

from __future__ import annotations

import json
import logging
from datetime import datetime

from tradingagents.dataflows.alpha_vantage_common import AlphaVantageNotConfiguredError
from tradingagents.dataflows.alpha_vantage_news import get_global_news, get_news

from ..config import FETCH_MULTIPLIER
from ..models import NewsArticle

logger = logging.getLogger(__name__)
VENDOR = "alpha_vantage"


def fetch_ticker_articles(
    ticker: str,
    *,
    start_date: str,
    end_date: str,
    limit: int,
) -> list[NewsArticle]:
    payload = get_news(ticker, start_date, end_date)
    return _parse_payload(payload, limit=limit * FETCH_MULTIPLIER)


def fetch_global_articles(
    *,
    curr_date: str,
    look_back_days: int,
    limit: int,
) -> list[NewsArticle]:
    payload = get_global_news(curr_date, look_back_days=look_back_days, limit=limit * FETCH_MULTIPLIER)
    return _parse_payload(payload, limit=limit * FETCH_MULTIPLIER)


def _parse_payload(payload: dict | str, *, limit: int) -> list[NewsArticle]:
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except json.JSONDecodeError:
            logger.warning("Alpha Vantage returned non-JSON payload")
            return []

    if not isinstance(payload, dict):
        return []

    feed = payload.get("feed") or []
    articles: list[NewsArticle] = []
    for item in feed:
        title = (item.get("title") or "").strip()
        if not title:
            continue
        articles.append(
            NewsArticle(
                title=title,
                summary=(item.get("summary") or "").strip(),
                link=(item.get("url") or "").strip(),
                source=(item.get("source") or "Alpha Vantage").strip(),
                vendor=VENDOR,
                pub_date=_parse_time_published(item.get("time_published")),
            )
        )
        if len(articles) >= limit:
            break
    return articles


def _parse_time_published(raw: str | None) -> datetime | None:
    if not raw:
        return None
    for fmt in ("%Y%m%dT%H%M%S", "%Y%m%dT%H%M"):
        try:
            return datetime.strptime(raw, fmt)
        except ValueError:
            continue
    return None


def is_configured() -> bool:
    try:
        from tradingagents.dataflows.alpha_vantage_common import get_api_key

        get_api_key()
        return True
    except AlphaVantageNotConfiguredError:
        return False
