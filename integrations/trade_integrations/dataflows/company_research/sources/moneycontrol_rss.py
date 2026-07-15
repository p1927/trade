"""Moneycontrol and India-results news feeds for calendar signals."""

from __future__ import annotations

import logging
import re
from typing import Any
from urllib.parse import quote_plus
from urllib.request import Request, urlopen

from trade_integrations.dataflows.rss_feeds import _parse_feed_entries, _strip_html

logger = logging.getLogger(__name__)

_UA = "trade-stack-research/0.1 (+https://github.com/p1927/trade)"

# Moneycontrol often 302s to login; keep as best-effort. Google News India is reliable.
MONEYCONTROL_RSS_URLS = (
    "https://www.moneycontrol.com/rss/results.xml",
    "https://www.moneycontrol.com/rss/latestnews.xml",
)

_RESULT_KEYWORDS = re.compile(
    r"\b(results?|earnings|board meeting|dividend|bonus|split|quarterly|q[1-4]|agm|egm)\b",
    re.I,
)


def _fetch_url(url: str, *, limit: int = 25) -> list[dict[str, str]]:
    try:
        request = Request(url, headers={"User-Agent": _UA})
        with urlopen(request, timeout=15) as response:
            raw = response.read()
        if not raw or b"<html" in raw[:200].lower():
            return []
        entries = _parse_feed_entries(raw, limit)
        return [
            {
                "title": entry.get("title", ""),
                "date": entry.get("date", ""),
                "summary": entry.get("summary", ""),
                "source": "moneycontrol_rss",
            }
            for entry in entries
            if entry.get("title")
        ]
    except Exception as exc:
        logger.info("RSS fetch failed for %s: %s", url, exc)
        return []


def _google_news_india(symbol: str, *, limit: int = 20) -> list[dict[str, str]]:
    query = quote_plus(
        f"{symbol} NSE results OR board meeting OR dividend site:moneycontrol.com OR site:economictimes.indiatimes.com"
    )
    url = (
        f"https://news.google.com/rss/search?q={query}"
        "&hl=en-IN&gl=IN&ceid=IN:en"
    )
    entries = _fetch_url(url, limit=limit)
    for entry in entries:
        entry["source"] = "google_news_india"
    return entries


def fetch_results_news(symbol: str, *, limit: int = 20) -> list[dict[str, Any]]:
    """Collect earnings/results-related headlines for one Indian ticker."""
    symbol_upper = symbol.strip().upper()
    seen: set[str] = set()
    events: list[dict[str, Any]] = []

    for url in MONEYCONTROL_RSS_URLS:
        for entry in _fetch_url(url, limit=limit):
            title = entry.get("title", "")
            if symbol_upper not in title.upper():
                continue
            if not _RESULT_KEYWORDS.search(title):
                continue
            key = title.lower()
            if key in seen:
                continue
            seen.add(key)
            events.append(
                {
                    "symbol": symbol_upper,
                    "company": "",
                    "type": "news_signal",
                    "purpose": "Results / corporate news",
                    "description": title,
                    "date": entry.get("date") or "",
                    "source": entry.get("source", "moneycontrol_rss"),
                }
            )

    for entry in _google_news_india(symbol_upper, limit=limit):
        title = _strip_html(entry.get("title", ""))
        if symbol_upper not in title.upper():
            continue
        if not _RESULT_KEYWORDS.search(title):
            continue
        key = title.lower()
        if key in seen:
            continue
        seen.add(key)
        events.append(
            {
                "symbol": symbol_upper,
                "company": "",
                "type": "news_signal",
                "purpose": "Results / corporate news",
                "description": title,
                "date": entry.get("date") or "",
                "source": "google_news_india",
            }
        )

    return events
