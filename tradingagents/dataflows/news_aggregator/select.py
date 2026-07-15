"""Balanced article selection across news backends."""

from __future__ import annotations

from datetime import datetime

from .dedup import article_key
from .models import NewsArticle


def _naive_dt(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value.replace(tzinfo=None) if value.tzinfo else value


def _primary_vendor(article: NewsArticle) -> str:
    return article.vendors[0] if article.vendors else article.vendor or "unknown"


def _sort_key(article: NewsArticle) -> datetime:
    return _naive_dt(article.pub_date) or datetime.min


def select_diverse_articles(articles: list[NewsArticle], limit: int) -> list[NewsArticle]:
    """Pick unique stories with fair representation from each backend.

    Fetches may return many more than ``limit`` articles upstream. This keeps
    the agent-facing output at ``limit`` unique stories while ensuring each
    configured backend contributes coverage when it has distinct articles.
    """
    if len(articles) <= limit:
        return articles

    by_vendor: dict[str, list[NewsArticle]] = {}
    for article in articles:
        by_vendor.setdefault(_primary_vendor(article), []).append(article)

    for vendor_articles in by_vendor.values():
        vendor_articles.sort(key=_sort_key, reverse=True)

    vendors = list(by_vendor.keys())
    quota = max(1, limit // len(vendors))
    selected: list[NewsArticle] = []
    used_keys: set[str] = set()

    for vendor in vendors:
        picked = 0
        for article in by_vendor[vendor]:
            key = article_key(article)
            if key and key in used_keys:
                continue
            selected.append(article)
            if key:
                used_keys.add(key)
            picked += 1
            if picked >= quota or len(selected) >= limit:
                break

    for article in articles:
        if len(selected) >= limit:
            break
        key = article_key(article)
        if key and key in used_keys:
            continue
        selected.append(article)
        if key:
            used_keys.add(key)

    selected.sort(key=_sort_key, reverse=True)
    return selected[:limit]
