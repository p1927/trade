"""Internal ingest adapters — use public ``news_hub_bridge`` package only."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)

_INDEX_ALIASES: dict[str, str] = {
    "^NSEI": "NIFTY",
    "^BSESN": "SENSEX",
    "NIFTY50": "NIFTY",
    "INDEX": "NIFTY",
    "MACRO": "NIFTY",
    "GLOBAL": "NIFTY",
}


def hub_ticker_for_symbol(symbol: str, *, kind: str = "ticker") -> str:
    """Map agent/market symbol to hub ``records.parquet`` ticker partition."""
    if kind == "global":
        return "NIFTY"
    raw = (symbol or "").strip().upper()
    if not raw:
        return "NIFTY"
    if raw in _INDEX_ALIASES:
        return _INDEX_ALIASES[raw]
    if raw.startswith("^"):
        return _INDEX_ALIASES.get(raw, raw.lstrip("^"))
    if "." in raw:
        return raw.split(".")[0]
    return raw


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def article_to_hub_row(article: Any, *, feed_url: str = "") -> dict[str, Any]:
    pub = ""
    pub_date = getattr(article, "pub_date", None)
    if pub_date is not None:
        pub = pub_date.isoformat() if hasattr(pub_date, "isoformat") else str(pub_date)
    link = str(getattr(article, "link", "") or "")
    sources: list[dict[str, Any]] = []
    for attr in getattr(article, "attributions", None) or []:
        sources.append(
            {
                "vendor": str(getattr(attr, "vendor", "") or "unknown"),
                "publisher": str(getattr(attr, "publisher", "") or "unknown"),
                "url": link,
                "fetched_at": _now_iso(),
            }
        )
    vendor = str(getattr(article, "vendor", "") or getattr(article, "source", "") or "news_aggregator")
    if not sources:
        sources = [
            {
                "vendor": vendor,
                "publisher": str(getattr(article, "source", "") or vendor),
                "url": link,
                "fetched_at": _now_iso(),
            }
        ]
    return {
        "title": str(getattr(article, "title", "") or ""),
        "summary": str(getattr(article, "summary", "") or ""),
        "url": link,
        "source": vendor,
        "published_at": pub or _now_iso(),
        "sources": sources,
        "feed_url": feed_url,
    }


def rss_entry_to_hub_row(
    entry: dict[str, Any],
    *,
    label: str,
    feed_url: str,
) -> dict[str, Any]:
    day = str(entry.get("date") or "").strip()
    published = f"{day}T09:00:00+00:00" if day and day != "?" else _now_iso()
    return {
        "title": str(entry.get("title") or ""),
        "summary": str(entry.get("summary") or ""),
        "url": feed_url,
        "source": f"rss:{label}",
        "published_at": published,
        "sources": [
            {
                "vendor": f"rss:{label}",
                "publisher": label,
                "url": feed_url,
                "fetched_at": _now_iso(),
            }
        ],
    }


def searxng_result_to_hub_row(result: dict[str, Any]) -> dict[str, Any]:
    engines = result.get("engines") or []
    source = ", ".join(str(e) for e in engines) if engines else "searxng"
    link = str(result.get("url") or "")
    pub = ""
    for key in ("publishedDate", "pubdate"):
        raw = result.get(key)
        if raw:
            pub = str(raw)
            break
    return {
        "title": str(result.get("title") or ""),
        "summary": str(result.get("content") or ""),
        "url": link,
        "source": f"searxng:{source}",
        "published_at": pub or _now_iso(),
        "sources": [
            {
                "vendor": "searxng",
                "publisher": source,
                "url": link,
                "fetched_at": _now_iso(),
            }
        ],
    }


def ingest_rows_to_hub(
    rows: list[dict[str, Any]],
    *,
    ticker: str,
    collection_day: str | None = None,
) -> dict[str, int]:
    if not rows:
        return {"ingested": 0, "cache_hits": 0, "verified": 0}
    try:
        from trade_integrations.dataflows.index_research.news_dedup import merge_raw_headlines
        from trade_integrations.dataflows.index_research.news_impact_engine import ingest_headline_rows

        hub_sym = hub_ticker_for_symbol(ticker)
        merged = merge_raw_headlines([r for r in rows if str(r.get("title") or "").strip()], ticker=hub_sym)
        if not merged:
            return {"ingested": 0, "cache_hits": 0, "verified": 0}
        return ingest_headline_rows(
            merged,
            ticker=hub_sym,
            collection_day=collection_day,
        )
    except Exception as exc:
        logger.debug("hub ingest skipped for %s: %s", ticker, exc)
        return {"ingested": len(rows), "error": 1}


def ingest_news_articles(
    articles: list[Any],
    *,
    ticker: str,
    kind: str = "ticker",
    collection_day: str | None = None,
) -> dict[str, int]:
    hub_sym = hub_ticker_for_symbol(ticker, kind=kind)
    rows = [article_to_hub_row(a) for a in articles if str(getattr(a, "title", "") or "").strip()]
    return ingest_rows_to_hub(rows, ticker=hub_sym, collection_day=collection_day)


def ingest_rss_entries(
    entries: list[dict[str, Any]],
    *,
    ticker: str,
    label: str,
    feed_url: str,
    collection_day: str | None = None,
) -> dict[str, int]:
    rows = [rss_entry_to_hub_row(e, label=label, feed_url=feed_url) for e in entries if e.get("title")]
    return ingest_rows_to_hub(rows, ticker=ticker, collection_day=collection_day)


def ingest_searxng_results(
    results: list[dict[str, Any]],
    *,
    ticker: str,
    kind: str = "ticker",
    collection_day: str | None = None,
) -> dict[str, int]:
    rows = [searxng_result_to_hub_row(r) for r in results if str(r.get("title") or "").strip()]
    hub_sym = hub_ticker_for_symbol(ticker, kind=kind)
    return ingest_rows_to_hub(rows, ticker=hub_sym, collection_day=collection_day)


def enrich_articles_with_hub_tags(articles: list[Any], *, ticker: str) -> list[Any]:
    try:
        from trade_integrations.dataflows.index_research.news_dedup import canonical_story_id
        from trade_integrations.dataflows.index_research.news_tags import (
            factors_from_record,
            topics_from_record,
        )
        from trade_integrations.hub_storage.verified_news_store import get_verified_record
    except Exception:
        return articles

    out: list[Any] = []
    for article in articles:
        title = str(getattr(article, "title", "") or "")
        link = str(getattr(article, "link", "") or "")
        story_id = canonical_story_id(title, link)
        rec = get_verified_record(story_id) if story_id else None
        if rec:
            topics = sorted(topics_from_record(rec))
            factors = factors_from_record(rec)[:4]
            bits: list[str] = []
            if topics:
                bits.append(f"topics: {', '.join(topics)}")
            if factors:
                bits.append(f"factors: {', '.join(factors)}")
            if bits:
                suffix = f"\n[verified hub tags — {'; '.join(bits)}]"
                current = str(getattr(article, "summary", "") or "")
                if suffix.strip() not in current:
                    article.summary = current + suffix
        out.append(article)
    return out
