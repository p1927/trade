"""Ingest constituent company-research headlines into hub (refresh-all-50 only)."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from trade_integrations.dataflows.company_research.models import CompanyResearchDoc


def _published_at(doc: CompanyResearchDoc) -> str:
    as_of = doc.as_of
    if isinstance(as_of, datetime):
        if as_of.tzinfo is None:
            as_of = as_of.replace(tzinfo=timezone.utc)
        return as_of.isoformat()
    return datetime.now(timezone.utc).isoformat()


def headline_rows_from_company_doc(doc: CompanyResearchDoc) -> list[dict[str, Any]]:
    """Extract hub-compatible headline rows from a company research doc."""
    news = doc.news if isinstance(doc.news, dict) else {}
    default_pub = _published_at(doc)
    rows: list[dict[str, Any]] = []
    seen_titles: set[str] = set()

    blocks = list(news.get("blocks") or [])
    if not blocks and news.get("headlines"):
        blocks = [{"headlines": news.get("headlines"), "source": news.get("source") or "news"}]

    for block in blocks:
        if not isinstance(block, dict):
            continue
        source = str(block.get("source") or "searxng")
        for item in block.get("headlines") or []:
            if isinstance(item, dict):
                title = str(item.get("title") or item.get("headline") or "").strip()
                summary = str(item.get("summary") or item.get("content") or "").strip()
                url = str(item.get("url") or item.get("link") or "").strip()
                pub = str(item.get("published_at") or item.get("published") or item.get("date") or default_pub)
            else:
                title = str(item).strip()
                summary = ""
                url = ""
                pub = default_pub
            if not title:
                continue
            key = title.lower()
            if key in seen_titles:
                continue
            seen_titles.add(key)
            rows.append(
                {
                    "title": title[:500],
                    "summary": summary[:2000],
                    "url": url,
                    "source": source,
                    "published_at": pub,
                }
            )
    return rows


def maybe_ingest_constituent_news(
    doc: CompanyResearchDoc,
    *,
    symbol: str,
    refresh: bool,
) -> dict[str, Any]:
    """Write constituent headlines to hub when user checked Refresh all 50."""
    if not refresh:
        return {"ingested": 0, "skipped": True, "reason": "refresh_false"}

    rows = headline_rows_from_company_doc(doc)
    if not rows:
        return {"ingested": 0, "skipped": True, "reason": "no_headlines"}

    from trade_integrations.dataflows.news_hub_bridge import hub_ticker_for_symbol, ingest_rows_to_hub

    hub_sym = hub_ticker_for_symbol(symbol.strip().upper())
    stats = ingest_rows_to_hub(rows, ticker=hub_sym)
    ingested = int(stats.get("ingested") or stats.get("queued") or 0)
    return {"ingested": ingested, "ticker": hub_sym, **stats}
