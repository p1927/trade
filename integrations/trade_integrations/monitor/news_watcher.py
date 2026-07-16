"""Detect material headlines that should trigger options plan refresh."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from trade_integrations.context.hub import get_hub_dir
from trade_integrations.dataflows.news_aggregator.models import NewsArticle
from trade_integrations.monitor.config import is_monitor_enabled

MATERIAL_KEYWORDS: tuple[str, ...] = (
    "earnings",
    "results",
    "guidance",
    "downgrade",
    "upgrade",
    "rbi",
    "budget",
    "fii",
    "vix",
    "merger",
    "circuit",
)

_TITLE_LINE_RE = re.compile(r"^### (.+?)(?:\s+\(source:.*\))?$", re.MULTILINE)
_LINK_LINE_RE = re.compile(r"^Link:\s*(.+)$", re.MULTILINE)


@dataclass(frozen=True)
class MaterialHeadline:
    title: str
    url: str
    fingerprint: str
    matched_keywords: tuple[str, ...]
    pub_date: datetime | None = None


def headline_fingerprint(title: str, url: str) -> str:
    """Return a stable fingerprint for deduplicating headlines."""
    normalized = f"{title.strip().lower()}|{url.strip().lower()}"
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]


def _news_seen_path(ticker: str) -> Path:
    return get_hub_dir() / "_data" / "news_seen" / f"{ticker.strip().upper()}.json"


def _load_seen_fingerprints(ticker: str) -> set[str]:
    path = _news_seen_path(ticker)
    if not path.is_file():
        return set()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return set()
    return {str(item) for item in payload.get("fingerprints") or []}


def _save_seen_fingerprints(ticker: str, fingerprints: set[str]) -> None:
    path = _news_seen_path(ticker)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "fingerprints": sorted(fingerprints),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _normalize_datetime(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def _article_after_since(article: NewsArticle, since: datetime) -> bool:
    if article.pub_date is None:
        return True
    pub = _normalize_datetime(article.pub_date)
    since_norm = _normalize_datetime(since)
    return pub >= since_norm


def _matched_keywords(text: str) -> tuple[str, ...]:
    lower = text.lower()
    return tuple(keyword for keyword in MATERIAL_KEYWORDS if keyword in lower)


def _is_material_article(article: NewsArticle) -> tuple[str, ...]:
    text = f"{article.title} {article.summary}".strip()
    return _matched_keywords(text)


def _fetch_ticker_articles(ticker: str, since: datetime) -> list[NewsArticle]:
    """Fetch ticker articles via the same aggregator pipeline as get_news_aggregated."""
    from tradingagents.dataflows.config import get_config
    from tradingagents.dataflows.symbol_utils import normalize_symbol

    from trade_integrations.dataflows.news_aggregator.aggregator import _merge_articles
    from trade_integrations.dataflows.news_aggregator.config import get_aggregator_sources

    since_norm = _normalize_datetime(since)
    end_dt = datetime.now(timezone.utc)
    start_date = since_norm.strftime("%Y-%m-%d")
    end_date = end_dt.strftime("%Y-%m-%d")

    config = get_config()
    articles = _merge_articles(
        kind="ticker",
        sources=get_aggregator_sources(),
        ticker=normalize_symbol(ticker),
        start_date=start_date,
        end_date=end_date,
        start_dt=since_norm.replace(tzinfo=None),
        end_dt=end_dt.replace(tzinfo=None),
        limit=config["news_article_limit"],
    )
    return [article for article in articles if _article_after_since(article, since_norm)]


def _parse_headlines_from_aggregated(markdown: str) -> list[tuple[str, str]]:
    """Parse title/url pairs from get_news_aggregated markdown output."""
    if not markdown or "No news found" in markdown:
        return []

    titles = _TITLE_LINE_RE.findall(markdown)
    links = _LINK_LINE_RE.findall(markdown)
    if not titles:
        return []

    pairs: list[tuple[str, str]] = []
    for index, title in enumerate(titles):
        url = links[index].strip() if index < len(links) else ""
        pairs.append((title.strip(), url))
    return pairs


def _fetch_headlines_via_aggregator(ticker: str, since: datetime) -> list[tuple[str, str]]:
    """Use get_news_aggregated for environments where article merge is unavailable."""
    from trade_integrations.dataflows.news_aggregator import get_news_aggregated

    since_norm = _normalize_datetime(since)
    end_dt = datetime.now(timezone.utc)
    markdown = get_news_aggregated(
        ticker,
        since_norm.strftime("%Y-%m-%d"),
        end_dt.strftime("%Y-%m-%d"),
    )
    return _parse_headlines_from_aggregated(markdown)


def check_material_news(ticker: str, since: datetime) -> list[MaterialHeadline]:
    """Return unseen material headlines for a ticker since the given timestamp."""
    if not is_monitor_enabled():
        return []

    symbol = ticker.strip().upper()
    if not symbol:
        return []

    seen = _load_seen_fingerprints(symbol)
    material: list[MaterialHeadline] = []

    try:
        articles = _fetch_ticker_articles(symbol, since)
    except Exception:
        articles = []

    candidates: list[tuple[str, str, datetime | None, tuple[str, ...]]] = []
    for article in articles:
        keywords = _is_material_article(article)
        if keywords:
            candidates.append((article.title, article.link, article.pub_date, keywords))

    if not candidates:
        for title, url in _fetch_headlines_via_aggregator(symbol, since):
            keywords = _matched_keywords(title)
            if keywords:
                candidates.append((title, url, None, keywords))

    for title, url, pub_date, keywords in candidates:
        fingerprint = headline_fingerprint(title, url)
        if fingerprint in seen:
            continue
        seen.add(fingerprint)
        material.append(
            MaterialHeadline(
                title=title,
                url=url,
                fingerprint=fingerprint,
                matched_keywords=keywords,
                pub_date=pub_date,
            )
        )

    if material:
        _save_seen_fingerprints(symbol, seen)
    return material
