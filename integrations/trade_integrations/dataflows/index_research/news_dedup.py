"""Cross-source headline deduplication and merge for index news."""

from __future__ import annotations

from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any

from trade_integrations.dataflows.news_aggregator.dedup import (
    article_key,
    normalize_title,
    normalize_url,
)


def publish_day_from_value(value: str, *, fallback: str = "") -> str:
    """Normalize publish timestamps (RFC, ISO, or YYYY-MM-DD) to YYYY-MM-DD."""
    text = (value or "").strip()
    if len(text) >= 10 and text[4] == "-" and text[7] == "-":
        return text[:10]
    try:
        if text:
            return parsedate_to_datetime(text).date().isoformat()
    except (TypeError, ValueError, OverflowError):
        pass
    fb = (fallback or "").strip()[:10]
    if len(fb) >= 10 and fb[4] == "-" and fb[7] == "-":
        return fb
    return fb


def publish_day_on_or_before(row: dict[str, Any], as_of_day: str) -> bool:
    """True when the row's publish day is on or before ``as_of_day`` (lookahead-safe)."""
    as_of = (as_of_day or "")[:10]
    if len(as_of) < 10:
        return True
    pub = publish_day_from_value(
        str(row.get("published_at") or ""),
        fallback=str(row.get("collection_day") or row.get("publish_day") or ""),
    )
    return bool(pub) and pub <= as_of


def filter_headlines_on_or_before(
    rows: list[dict[str, Any]],
    as_of_day: str,
) -> tuple[list[dict[str, Any]], int]:
    """Drop rows published after ``as_of_day``; returns (kept, skipped_count)."""
    kept: list[dict[str, Any]] = []
    skipped = 0
    for row in rows:
        if publish_day_on_or_before(row, as_of_day):
            kept.append(row)
        else:
            skipped += 1
    return kept, skipped


def normalize_published_at(value: str, *, fallback_day: str = "") -> str:
    """Return ISO-8601 published_at; preserves time when parseable."""
    text = (value or "").strip()
    day = publish_day_from_value(text, fallback=fallback_day)
    if not day:
        return text
    if "T" in text and text[4] == "-" and text[7] == "-":
        return text[:32]
    try:
        if text and text[4] != "-":
            dt = parsedate_to_datetime(text)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.isoformat()
    except (TypeError, ValueError, OverflowError):
        pass
    return f"{day}T09:00:00+00:00"


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


def _parse_published_dt(value: str) -> datetime | None:
    text = (value or "").strip()
    if not text:
        return None
    if "T" in text and text[4] == "-" and text[7] == "-":
        try:
            normalized = text[:-1] + "+00:00" if text.endswith("Z") else text
            dt = datetime.fromisoformat(normalized)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        except (TypeError, ValueError, OverflowError):
            return None
    try:
        if text[4] == "-":
            dt = datetime.fromisoformat(f"{text[:10]}T09:00:00+00:00")
            return dt
        dt = parsedate_to_datetime(text)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except (TypeError, ValueError, OverflowError, IndexError):
        return None


def _pick_best_published(a: str, b: str) -> str:
    a = (a or "").strip()
    b = (b or "").strip()
    if not a:
        return b
    if not b:
        return a
    dt_a = _parse_published_dt(a)
    dt_b = _parse_published_dt(b)
    if dt_a and dt_b:
        return b if dt_b >= dt_a else a
    return b or a


def _sources_from_row(row: dict[str, Any]) -> list[dict[str, Any]]:
    existing = row.get("sources")
    if isinstance(existing, list) and existing:
        return [_source_entry({**row, **src}) if isinstance(src, dict) else _source_entry(row) for src in existing]
    return [_source_entry(row)]


_BULLISH_THEMES = frozenset({"rally", "recovery", "record_high"})
_BEARISH_THEMES = frozenset({"crash", "selloff", "record_low"})


def _market_direction(themes: list[str]) -> str:
    bulls = [theme for theme in themes if theme in _BULLISH_THEMES]
    bears = [theme for theme in themes if theme in _BEARISH_THEMES]
    if bulls and bears:
        return "mixed"
    if bears:
        return "bearish"
    if bulls:
        return "bullish"
    if "flat" in themes:
        return "flat"
    if "volatility_spike" in themes:
        return "volatile"
    return "neutral"


def semantic_cluster_key(row: dict[str, Any], *, ticker: str = "NIFTY") -> str:
    """Cluster key from publish day + topic + market direction + primary factor."""
    tags = _tags_from_row(row, ticker=ticker)
    day = str(tags.get("publish_day") or "").strip()
    if not day:
        day = publish_day_from_value(str(row.get("published_at") or ""))
    if not day:
        return ""

    topics = sorted(tags.get("topics") or [])
    if not topics:
        return ""

    direction = _market_direction(list(tags.get("themes") or []))
    primary_factor = str((tags.get("factors") or ["index_sentiment"])[0])
    return f"sem:{day}:{topics[0]}:{direction}:{primary_factor}"


def _tags_from_row(row: dict[str, Any], *, ticker: str = "NIFTY") -> dict[str, Any]:
    existing = row.get("tags")
    if isinstance(existing, dict) and any(
        existing.get(key) for key in ("topics", "factors", "themes")
    ):
        return existing
    from trade_integrations.dataflows.index_research.news_tags import build_article_tags

    return build_article_tags(
        str(row.get("title") or ""),
        str(row.get("summary") or ""),
        ticker=ticker,
        published_at=str(row.get("published_at") or ""),
    ).to_dict()


def merge_raw_headlines(rows: list[dict[str, Any]], *, ticker: str = "NIFTY") -> list[dict[str, Any]]:
    """Merge duplicate stories across sources into canonical rows with sources[] and tags."""
    from trade_integrations.dataflows.index_research.news_tags import merge_article_tags, tags_from_dict

    merged: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    title_to_key: dict[str, str] = {}

    for row in rows:
        title = str(row.get("title") or "").strip()
        if not title:
            continue
        url = str(row.get("url") or "")
        preset_id = str(row.get("canonical_story_id") or "").strip()
        key = preset_id or canonical_story_id(title, url)
        if not key:
            key = f"title:{normalize_title(title)}"
        if not key:
            continue

        title_norm = normalize_title(title)
        if title_norm and title_norm in title_to_key:
            key = title_to_key[title_norm]

        if title_norm:
            title_to_key[title_norm] = key

        src_entries = _sources_from_row(row)
        if key not in merged:
            order.append(key)
            merged[key] = {
                "canonical_story_id": key,
                "id": key,
                "title": title,
                "summary": str(row.get("summary") or ""),
                "url": url,
                "source": src_entries[0]["vendor"],
                "published_at": str(row.get("published_at") or ""),
                "sources": list(src_entries),
                "tags": _tags_from_row(row, ticker=ticker),
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
        for src in src_entries:
            if not any(s.get("url") == src["url"] and s.get("vendor") == src["vendor"] for s in sources):
                sources.append(src)
        existing["sources"] = sources
        existing["tags"] = merge_article_tags(
            tags_from_dict(existing.get("tags")),
            tags_from_dict(_tags_from_row(row, ticker=ticker)),
        ).to_dict()

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
