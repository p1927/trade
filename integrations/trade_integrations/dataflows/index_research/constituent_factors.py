"""Extract per-constituent driver factors from company research."""

from __future__ import annotations

from typing import Any

from trade_integrations.dataflows.company_research.models import CompanyResearchDoc

_SECTOR_MACRO_LINKS: dict[str, list[tuple[str, str]]] = {
    "energy": [("oil_brent", "Brent crude exposure"), ("usd_inr", "USD/INR import costs")],
    "oil": [("oil_brent", "Brent crude exposure")],
    "financial": [("repo_rate", "Rate-sensitive lending"), ("fii_net_5d", "FII flows into banks")],
    "bank": [("repo_rate", "Rate-sensitive lending"), ("fii_net_5d", "FII flows into banks")],
    "it": [("usd_inr", "USD revenue translation"), ("sp500", "Global tech risk appetite")],
    "technology": [("usd_inr", "USD revenue translation"), ("sp500", "Global tech risk appetite")],
    "pharma": [("usd_inr", "Export revenue"), ("sp500", "Global risk sentiment")],
    "healthcare": [("usd_inr", "Export revenue")],
    "metal": [("usd_inr", "Commodity import costs"), ("oil_brent", "Energy input costs")],
    "auto": [("oil_brent", "Fuel demand proxy"), ("usd_inr", "Import component costs")],
}

_EARNINGS_TYPES = frozenset({"results", "earnings", "earnings_signal"})


def _coerce_text(value: Any, *, limit: int = 400) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        text = value.strip()
        return text[:limit] if text else None
    if isinstance(value, (int, float, bool)):
        return str(value)
    if isinstance(value, dict):
        for key in ("summary", "text", "headline", "title", "description", "rationale", "purpose"):
            nested = _coerce_text(value.get(key), limit=limit)
            if nested:
                return nested
        if value.get("positive_pct") is not None or value.get("negative_pct") is not None:
            pos = float(value.get("positive_pct") or 0)
            neg = float(value.get("negative_pct") or 0)
            neu = float(value.get("neutral_pct") or 0)
            return f"Sentiment mix: {pos:.0f}% positive, {neg:.0f}% negative, {neu:.0f}% neutral"
        return None
    if isinstance(value, list):
        parts = [_coerce_text(item, limit=120) for item in value[:3]]
        joined = " · ".join(part for part in parts if part)
        return joined[:limit] if joined else None
    return str(value)[:limit]


def _sector_macro_links(sector: str) -> list[dict[str, Any]]:
    key = sector.lower().strip()
    factors: list[dict[str, Any]] = []
    for token, links in _SECTOR_MACRO_LINKS.items():
        if token in key:
            for macro, note in links:
                factors.append(
                    {
                        "type": "macro",
                        "factor": macro,
                        "macro_link": macro,
                        "note": note,
                        "source": "sector_map",
                    }
                )
    return factors


def _news_headlines(news: dict[str, Any], *, limit: int = 2) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    items: list[Any] = list(news.get("headlines") or [])
    for block in news.get("blocks") or []:
        if isinstance(block, dict):
            items.extend(block.get("headlines") or [])
    if not items:
        items = list(news.get("items") or news.get("articles") or [])
    for item in items[:limit]:
        if not isinstance(item, dict):
            continue
        headline = item.get("title") or item.get("headline") or item.get("summary")
        if not headline:
            continue
        rows.append(
            {
                "type": "news",
                "headline": _coerce_text(headline, limit=200),
                "date": _coerce_text(item.get("published") or item.get("date"), limit=40),
                "source": _coerce_text(item.get("source") or "news", limit=80),
            }
        )
    return rows


def _earnings_factors(earnings: dict[str, Any]) -> list[dict[str, Any]]:
    if not earnings:
        return []
    rows: list[dict[str, Any]] = []
    signal = str(earnings.get("signal") or earnings.get("view") or "").lower()
    if signal:
        rows.append(
            {
                "type": "earnings",
                "impact": signal,
                "note": _coerce_text(earnings.get("rationale") or earnings.get("summary")),
                "source": _coerce_text(earnings.get("source") or "earnings_signal", limit=80),
            }
        )
    return rows


def _calendar_factors(events: list[dict[str, Any]], *, limit: int = 3) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for event in events[:limit]:
        event_type = str(event.get("type") or event.get("purpose") or "calendar")
        rows.append(
            {
                "type": "calendar",
                "event": _coerce_text(event_type, limit=80),
                "date": _coerce_text(event.get("date"), limit=40),
                "note": _coerce_text(event.get("description") or event.get("purpose")),
                "source": _coerce_text(event.get("source") or "calendar", limit=80),
            }
        )
        if any(t in event_type.lower() for t in _EARNINGS_TYPES):
            rows.append(
                {
                    "type": "earnings",
                    "date": event.get("date"),
                    "event": event_type,
                    "note": "Earnings within horizon",
                    "source": event.get("source") or "calendar",
                }
            )
    return rows


def build_constituent_factors(
    doc: CompanyResearchDoc,
    *,
    sector: str,
    upcoming_events: list[dict[str, Any]],
    sentiment_score: float | None,
) -> list[dict[str, Any]]:
    """Build structured driver list for one constituent."""
    factors: list[dict[str, Any]] = []

    if sentiment_score is not None:
        factors.append(
            {
                "type": "news_sentiment",
                "score": sentiment_score,
                "note": _coerce_text((doc.sentiment or {}).get("summary") if isinstance(doc.sentiment, dict) else None),
                "source": _coerce_text((doc.sentiment or {}).get("source") or "finbert", limit=80),
            }
        )

    factors.extend(_earnings_factors(doc.earnings_signal or {}))
    factors.extend(_calendar_factors(upcoming_events))
    factors.extend(_news_headlines(doc.news or {}))

    macro = doc.macro or {}
    if isinstance(macro, dict) and macro.get("india_vix") is not None:
        factors.append(
            {
                "type": "macro",
                "factor": "india_vix",
                "value": macro.get("india_vix"),
                "note": "India VIX at research time",
                "source": macro.get("source") or "macro",
            }
        )

    factors.extend(_sector_macro_links(sector))
    return factors[:12]
