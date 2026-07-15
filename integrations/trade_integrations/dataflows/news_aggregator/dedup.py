"""Deduplication helpers for merged news articles."""

from __future__ import annotations

import re
from urllib.parse import urlparse

from .models import NewsArticle, SourceAttribution

_NON_ALNUM = re.compile(r"[^a-z0-9]+")


def normalize_title(title: str) -> str:
    """Lowercase, strip punctuation, collapse whitespace for fuzzy matching."""
    collapsed = _NON_ALNUM.sub(" ", title.lower()).strip()
    return " ".join(collapsed.split())


def normalize_url(url: str) -> str:
    """Canonicalize a URL for duplicate detection."""
    url = (url or "").strip()
    if not url:
        return ""
    parsed = urlparse(url.lower())
    path = parsed.path.rstrip("/")
    return f"{parsed.netloc}{path}"


def article_key(article: NewsArticle) -> str:
    url_key = normalize_url(article.link)
    if url_key:
        return f"url:{url_key}"
    title_key = normalize_title(article.title)
    if title_key:
        return f"title:{title_key}"
    return ""


def _merge_attributions(existing: NewsArticle, incoming: NewsArticle) -> None:
    for attr in incoming.attributions:
        if attr not in existing.attributions:
            existing.attributions.append(attr)
    for vendor in incoming.vendors:
        if vendor and vendor not in existing.vendors:
            existing.vendors.append(vendor)


def _merge_into(existing: NewsArticle, incoming: NewsArticle) -> None:
    """Combine duplicate coverage of the same story across backends."""
    _merge_attributions(existing, incoming)

    if incoming.summary and len(incoming.summary) > len(existing.summary):
        existing.summary = incoming.summary
    if incoming.link and not existing.link:
        existing.link = incoming.link
    if incoming.pub_date and (
        not existing.pub_date
        or incoming.pub_date > existing.pub_date
    ):
        existing.pub_date = incoming.pub_date

    publishers = [attr.publisher for attr in existing.attributions]
    existing.source = ", ".join(publishers)


def deduplicate_articles(articles: list[NewsArticle]) -> list[NewsArticle]:
    """Merge duplicate articles and stack publisher/backend attribution."""
    merged: list[NewsArticle] = []
    index: dict[str, int] = {}

    for article in articles:
        key = article_key(article)
        if not key:
            merged.append(article)
            continue

        if key not in index:
            index[key] = len(merged)
            merged.append(article)
            continue

        _merge_into(merged[index[key]], article)

    return merged
