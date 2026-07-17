"""Unified headline collection (internal — ingest via ``news_hub_bridge``)."""

from __future__ import annotations

import hashlib
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from trade_integrations.context.hub import get_hub_dir
from trade_integrations.dataflows.index_research.company_news_backfill import (
    _fetch_rss_headlines,
    _google_news_rss_url,
)
from trade_integrations.monitor.news_watcher import headline_fingerprint
from trade_integrations.dataflows.index_research.news_dedup import (
    merge_raw_headlines,
    normalize_published_at,
)

_NEWS_DAILY = Path("_data") / "news" / "daily"


def _headline_id(title: str, url: str = "", published_at: str = "") -> str:
    raw = f"{title}|{url}|{published_at}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def _load_archive_headlines(day: str, *, symbol: str = "NIFTY", limit: int = 12) -> list[dict[str, Any]]:
    path = get_hub_dir() / _NEWS_DAILY / f"{day[:10]}.parquet"
    if not path.is_file():
        return []
    try:
        import pandas as pd

        frame = pd.read_parquet(path)
    except Exception:
        return []
    if frame.empty:
        return []
    sym = symbol.strip().upper()
    if "symbol" in frame.columns:
        subset = frame[frame["symbol"].astype(str).str.upper().isin({sym, "NIFTY", "INDEX"})]
        if subset.empty:
            subset = frame
    else:
        subset = frame
    rows: list[dict[str, Any]] = []
    for _, row in subset.head(limit).iterrows():
        title = str(row.get("title") or "").strip()
        if not title:
            continue
        url = str(row.get("url") or row.get("link") or "")
        summary = str(row.get("summary") or "")
        published = normalize_published_at(
            str(row.get("published_at") or row.get("captured_at") or ""),
            fallback_day=day,
        )
        rows.append(
            {
                "id": _headline_id(title, url, published),
                "title": title,
                "summary": summary,
                "url": url,
                "source": str(row.get("source") or "news_archive"),
                "published_at": published[:32],
                "fingerprint": headline_fingerprint(title, url or title),
            }
        )
    return rows


def _fetch_aggregator_headlines(ticker: str, day: str, *, limit: int = 8) -> list[dict[str, Any]]:
    try:
        from trade_integrations.dataflows.news_aggregator import get_news_aggregated

        end = day[:10]
        start = (date.fromisoformat(end) - timedelta(days=2)).isoformat()
        text = get_news_aggregated(ticker, start_date=start, end_date=end, limit=limit)
    except Exception:
        return []
    if not text:
        return []
    rows: list[dict[str, Any]] = []
    for block in text.split("\n\n"):
        lines = [ln.strip() for ln in block.splitlines() if ln.strip()]
        if not lines:
            continue
        title = lines[0].lstrip("#").strip()
        summary = ""
        url = ""
        for ln in lines[1:]:
            if ln.lower().startswith("summary:"):
                summary = ln.split(":", 1)[-1].strip()
            elif ln.lower().startswith("link:"):
                url = ln.split(":", 1)[-1].strip()
        if not title:
            continue
        rows.append(
            {
                "id": _headline_id(title, url, day),
                "title": title,
                "summary": summary,
                "url": url,
                "source": "news_aggregator",
                "published_at": f"{day}T12:00:00+00:00",
                "fingerprint": headline_fingerprint(title, url or title),
            }
        )
        if len(rows) >= limit:
            break
    return rows


def collect_headlines_for_day(
    day: str,
    *,
    ticker: str = "NIFTY",
    limit: int = 12,
) -> list[dict[str, Any]]:
    """Collect headlines for a calendar day; archive first, then RSS/aggregator."""
    seen: set[str] = set()
    out: list[dict[str, Any]] = []

    def _add(row: dict[str, Any]) -> None:
        title = str(row.get("title") or "").strip()
        if not title:
            return
        fp = str(row.get("fingerprint") or headline_fingerprint(title, str(row.get("url") or title)))
        if fp in seen:
            return
        seen.add(fp)
        if "id" not in row:
            row["id"] = _headline_id(title, str(row.get("url") or ""), str(row.get("published_at") or day))
        if "fingerprint" not in row:
            row["fingerprint"] = fp
        out.append(row)

    for row in _load_archive_headlines(day, symbol=ticker, limit=limit):
        _add(row)
    if len(out) >= limit:
        return merge_raw_headlines(out[:limit], ticker=ticker)

    for row in _fetch_aggregator_headlines(ticker, day, limit=limit):
        _add(row)
    if len(out) >= limit:
        return merge_raw_headlines(out[:limit], ticker=ticker)

    after = day[:10]
    try:
        before = (date.fromisoformat(after) + timedelta(days=1)).isoformat()
    except ValueError:
        before = after
    url = _google_news_rss_url("India stock market Nifty", after=after, before=before)
    for row in _fetch_rss_headlines(url, limit=limit):
        _add(
            {
                "title": row.get("title") or "",
                "summary": "",
                "url": row.get("url") or "",
                "source": row.get("source") or "google_news_rss",
                "published_at": row.get("published") or f"{day}T09:00:00+00:00",
            }
        )
        if len(out) >= limit:
            break

    return merge_raw_headlines(out[:limit], ticker=ticker)


def collect_headlines_for_window(
    start: str,
    end: str,
    *,
    ticker: str = "NIFTY",
    limit_per_day: int = 6,
    max_total: int = 40,
) -> list[dict[str, Any]]:
    try:
        start_d = date.fromisoformat(start[:10])
        end_d = date.fromisoformat(end[:10])
    except ValueError:
        return collect_headlines_for_day(start, ticker=ticker, limit=max_total)

    rows: list[dict[str, Any]] = []
    day = start_d
    while day <= end_d and len(rows) < max_total:
        rows.extend(collect_headlines_for_day(day.isoformat(), ticker=ticker, limit=limit_per_day))
        day += timedelta(days=1)
    return merge_raw_headlines(rows[:max_total], ticker=ticker)
