"""Build T0 headline event flags for factor enrichment."""

from __future__ import annotations

from typing import Any

from trade_integrations.dataflows.index_research.news_tags import topics_from_record


def headline_event_flags_for_day(day: str) -> dict[str, float]:
    """Return geopolitical/oil headline flags (0/1) for a trading date."""
    from trade_integrations.dataflows.index_research.causal_attribution import (
        _fetch_index_headlines,
    )

    headlines = _fetch_index_headlines(day)
    topics: set[str] = set()
    for item in headlines:
        if not isinstance(item, dict):
            continue
        tagged = topics_from_record(item)
        topics |= set(tagged.get("topics") or [])
        title = str(item.get("title") or item.get("headline") or "").lower()
        if any(k in title for k in ("war", "conflict", "missile", "geopolit")):
            topics.add("war")
        if any(k in title for k in ("oil", "crude", "brent", "opec")):
            topics.add("oil")

    return {
        "geopolitical_headline_flag": 1.0 if "war" in topics else 0.0,
        "oil_headline_flag": 1.0 if "oil" in topics else 0.0,
    }


def build_headline_flag_series(trading_dates: list[str]) -> dict[str, dict[str, float]]:
    """Precompute flags for all trading dates (best-effort, no network in tests)."""
    out: dict[str, dict[str, float]] = {}
    for day in trading_dates:
        try:
            out[day] = headline_event_flags_for_day(day)
        except Exception:
            out[day] = {"geopolitical_headline_flag": 0.0, "oil_headline_flag": 0.0}
    return out
