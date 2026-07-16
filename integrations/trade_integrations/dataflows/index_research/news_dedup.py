"""Cross-source headline deduplication and merge for index news."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from trade_integrations.dataflows.news_aggregator.dedup import (
    article_key,
    normalize_title,
    normalize_url,
)


def canonical_story_id(title: str, url: str = "") -> str:
    """Stable story key: prefer normalized URL, else normalized title."""
    url_key = normalize_url(url)
    if url_key:
        return f"url:{url_key}"
    title_key = normalize_title(title)
    if title_key:
        return f"title:{title_key}"
    return ""


def _source_entry(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "vendor": str(row.get("source") or row.get("vendor") or "unknown"),
        "publisher": str(row.get("publisher") or row.get("source") or "unknown"),
        "url": str(row.get("url") or ""),
        "fetched_at": str(row.get("fetched_at") or datetime.now(timezone.utc).isoformat()),
    }


def _pick_best_summary(a: str, b: str) -> str:
    a = (a or "").strip()
    b = (b or "").strip()
    if len(b) > len(a):
        return b
    return a


def _pick_best_published(a: str, b: str) -> str:
    a = (a or "").strip()
    b = (b or "").strip()
    return b or a


def merge_raw_headlines(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Merge duplicate stories across sources into canonical rows with sources[]."""
    merged: dict[str, dict[str, Any]] = {}
    order: list[str] = []

    for row in rows:
        title = str(row.get("title") or "").strip()
        if not title:
            continue
        url = str(row.get("url") or "")
        key = canonical_story_id(title, url)
        if not key:
            key = f"title:{normalize_title(title)}"
        if not key:
            continue

        src = _source_entry(row)
        if key not in merged:
            order.append(key)
            merged[key] = {
                "canonical_story_id": key,
                "id": key,
                "title": title,
                "summary": str(row.get("summary") or ""),
                "url": url,
                "source": src["vendor"],
                "published_at": str(row.get("published_at") or ""),
                "sources": [src],
                "fingerprint": row.get("fingerprint"),
            }
            continue

        existing = merged[key]
        existing["summary"] = _pick_best_summary(existing.get("summary", ""), str(row.get("summary") or ""))
        existing["url"] = existing.get("url") or url
        existing["published_at"] = _pick_best_published(
            str(existing.get("published_at") or ""),
            str(row.get("published_at") or ""),
        )
        sources: list[dict[str, Any]] = list(existing.get("sources") or [])
        if not any(s.get("url") == src["url"] and s.get("vendor") == src["vendor"] for s in sources):
            sources.append(src)
        existing["sources"] = sources

    return [merged[k] for k in order]


def story_key_from_row(row: dict[str, Any]) -> str:
    """Return canonical_story_id for a raw or merged row."""
    if row.get("canonical_story_id"):
        return str(row["canonical_story_id"])
    return canonical_story_id(str(row.get("title") or ""), str(row.get("url") or ""))


def sources_changed(cached: dict[str, Any], incoming: dict[str, Any]) -> bool:
    """True when incoming row adds a new source or longer summary."""
    cached_sources = {f"{s.get('vendor')}|{s.get('url')}" for s in (cached.get("sources") or [])}
    for src in incoming.get("sources") or [_source_entry(incoming)]:
        key = f"{src.get('vendor')}|{src.get('url')}"
        if key not in cached_sources:
            return True
    cached_summary = str(cached.get("content_summary") or "")
    incoming_summary = str(incoming.get("summary") or "")
    return len(incoming_summary) > len(cached_summary) + 20
