"""Configurable RSS/Atom feed fetcher for sentiment analysis.

Feeds are defined in ``default_config.sentiment_rss_feeds`` and can be
extended via ``TRADINGAGENTS_SENTIMENT_RSS_FEEDS`` in ``.env``. Each entry
is a ``label|url`` pair (comma-separated); bare URLs are accepted and
labelled from the hostname.

URLs may include ``{ticker}`` (upper-cased symbol) and ``{search_term}``
(crypto base for crypto pairs, otherwise upper-cased symbol) for
ticker-specific feeds. Static feeds omit placeholders.

Every feed is fetched best-effort on each run; failures degrade to a
placeholder string so callers always get a uniform string return type.
"""

from __future__ import annotations

import html
import http.client
import logging
import os
import re
import time
import xml.etree.ElementTree as ET
from datetime import datetime
from email.utils import parsedate_to_datetime
from urllib.error import HTTPError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from tradingagents.dataflows.config import get_config
from tradingagents.dataflows.symbol_utils import crypto_base

logger = logging.getLogger(__name__)

_UA = "tradingagents/0.2 (+https://github.com/TauricResearch/TradingAgents)"
_ATOM_NS = {"atom": "http://www.w3.org/2005/Atom"}


def _strip_html(content: str) -> str:
    if not content:
        return ""
    text = re.sub(r"<[^>]+>", " ", content)
    return " ".join(html.unescape(text).split())


def _parse_pub_date(raw: str | None) -> str:
    if not raw:
        return "?"
    try:
        if "T" in raw:
            normalized = raw[:-1] + "+00:00" if raw.endswith("Z") else raw
            return datetime.fromisoformat(normalized).strftime("%Y-%m-%d")
    except (ValueError, TypeError, OverflowError):
        pass
    try:
        return parsedate_to_datetime(raw).strftime("%Y-%m-%d")
    except (ValueError, TypeError, OverflowError):
        return "?"


def _label_from_url(url: str) -> str:
    host = urlparse(url).hostname or "feed"
    return host.removeprefix("www.")


def _atom_entry_link(entry: ET.Element) -> str:
    """Prefer alternate article URL; fall back to first link href."""
    fallback = ""
    for link_el in entry.findall("atom:link", _ATOM_NS):
        href = (link_el.get("href") or "").strip()
        if not href:
            continue
        rel = (link_el.get("rel") or "alternate").strip().lower()
        if rel == "alternate":
            return href
        if not fallback:
            fallback = href
    return fallback


def _rss_item_link(item: ET.Element) -> str:
    link_el = item.find("link")
    if link_el is not None:
        text = (link_el.text or "").strip()
        if text:
            return text
    guid_el = item.find("guid")
    if guid_el is not None:
        guid = (guid_el.text or "").strip()
        if guid and not guid.startswith("http://") and "://" not in guid:
            return ""
        return guid
    return ""


def _parse_feed_entries(raw_xml: bytes, limit: int) -> list[dict]:
    root = ET.fromstring(raw_xml)
    entries: list[dict] = []

    # Atom
    for entry in root.findall("atom:entry", _ATOM_NS)[:limit]:
        title_el = entry.find("atom:title", _ATOM_NS)
        published_el = entry.find("atom:published", _ATOM_NS)
        updated_el = entry.find("atom:updated", _ATOM_NS)
        summary_el = entry.find("atom:summary", _ATOM_NS)
        content_el = entry.find("atom:content", _ATOM_NS)
        body = ""
        if summary_el is not None and summary_el.text:
            body = summary_el.text
        elif content_el is not None and content_el.text:
            body = content_el.text
        entries.append({
            "title": (title_el.text if title_el is not None else "") or "",
            "date": _parse_pub_date(
                (published_el.text if published_el is not None else None)
                or (updated_el.text if updated_el is not None else None)
            ),
            "summary": _strip_html(body),
            "url": _atom_entry_link(entry),
        })

    if entries:
        return entries

    # RSS 2.0
    channel = root.find("channel")
    if channel is None:
        return []
    for item in channel.findall("item")[:limit]:
        title_el = item.find("title")
        pub_el = item.find("pubDate")
        desc_el = item.find("description")
        entries.append({
            "title": (title_el.text if title_el is not None else "") or "",
            "date": _parse_pub_date(pub_el.text if pub_el is not None else None),
            "summary": _strip_html(desc_el.text if desc_el is not None else ""),
            "url": _rss_item_link(item),
        })
    return entries


def _parse_feed_spec(raw: str) -> list[dict[str, str]]:
    """Parse ``label|url`` pairs (or bare URLs) from a comma-separated string."""
    feeds: list[dict[str, str]] = []
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        if "|" in part:
            label, url = part.split("|", 1)
            label, url = label.strip(), url.strip()
        else:
            url = part
            label = _label_from_url(url)
        if url:
            feeds.append({"label": label or _label_from_url(url), "url": url})
    return feeds


def get_sentiment_rss_feeds() -> list[dict[str, str]]:
    """Return built-in feeds plus any extras from ``TRADINGAGENTS_SENTIMENT_RSS_FEEDS``."""
    feeds = list(get_config().get("sentiment_rss_feeds") or [])
    extra = os.environ.get("TRADINGAGENTS_SENTIMENT_RSS_FEEDS", "").strip()
    if extra:
        feeds.extend(_parse_feed_spec(extra))
    return feeds


def _resolve_url(url: str, ticker: str) -> str:
    search_term = (crypto_base(ticker) or ticker).strip().upper()
    return (
        url.replace("{search_term}", search_term)
        .replace("{ticker}", ticker.strip().upper())
    )


def _fetch_one_feed(
    label: str,
    url: str,
    limit: int,
    timeout: float,
    inter_request_delay: float,
    is_first: bool,
) -> tuple[str, list[dict]]:
    if not is_first and inter_request_delay > 0:
        time.sleep(inter_request_delay)

    req = Request(url, headers={"User-Agent": _UA})
    try:
        with urlopen(req, timeout=timeout) as resp:
            entries = _parse_feed_entries(resp.read(), limit)
    except HTTPError as exc:
        logger.warning("RSS feed %s fetch failed (%s): %s", label, url, exc)
        return f"{label}: <unavailable: HTTP {exc.code}>", []
    except (OSError, http.client.HTTPException, ET.ParseError) as exc:
        logger.warning("RSS feed %s fetch failed (%s): %s", label, url, exc)
        return f"{label}: <unavailable: {type(exc).__name__}>", []

    if not entries:
        return f"{label}: <no items found>", []

    lines = [f"{label} — {len(entries)} recent items:"]
    for entry in entries:
        title = (entry.get("title") or "").replace("\n", " ").strip()
        summary = (entry.get("summary") or "").replace("\n", " ").strip()
        if len(summary) > 240:
            summary = summary[:240] + "…"
        date = entry.get("date") or "?"
        lines.append(f"  [{date}] {title}")
        if summary:
            lines.append(f"    {summary}")
    return "\n".join(lines), entries


def fetch_rss_feeds(
    ticker: str,
    limit_per_feed: int = 10,
    timeout: float = 10.0,
    inter_request_delay: float = 0.5,
) -> str:
    """Fetch all configured RSS/Atom feeds for ``ticker`` and return a plaintext block."""
    feeds = get_sentiment_rss_feeds()
    if not feeds:
        return "<no RSS feeds configured>"

    blocks = []
    for i, feed in enumerate(feeds):
        label = feed.get("label") or _label_from_url(feed["url"])
        url = _resolve_url(feed["url"], ticker)
        block, entries = _fetch_one_feed(
            label,
            url,
            limit_per_feed,
            timeout,
            inter_request_delay,
            is_first=(i == 0),
        )
        blocks.append(block)
        if entries:
            try:
                from trade_integrations.dataflows.news_hub_bridge import ingest_rss_entries

                ingest_rss_entries(entries, ticker=ticker, label=label, feed_url=url)
            except Exception as exc:
                logger.debug("hub ingest RSS skipped for %s: %s", label, exc)

    if all("<unavailable" in block or "<no items found>" in block for block in blocks):
        return (
            f"<no RSS feed data available for {ticker.upper()} "
            f"({len(feeds)} feed(s) tried)>"
        )
    return "\n\n".join(blocks)
