"""Parent/child threading for multi-day macro stories (Phase 3)."""

from __future__ import annotations

import os
import re
from typing import Any

from trade_integrations.dataflows.index_research.news_dedup import publish_day_from_value

_MACRO_PARENT_TOPICS = frozenset(
    {
        "geopolitical",
        "rate_cycle",
        "earnings_season",
        "oil",
        "fed",
        "rbi",
        "war",
        "sanctions",
        "trade_war",
        "inflation",
    }
)

_SLUG_SAFE = re.compile(r"[^a-z0-9]+")


def parent_topics_from_env() -> frozenset[str]:
    raw = os.getenv("HUB_NEWS_PARENT_TOPICS", "").strip()
    if not raw:
        return _MACRO_PARENT_TOPICS
    return frozenset(t.strip().lower() for t in raw.split(",") if t.strip())


def _slug_part(text: str) -> str:
    slug = _SLUG_SAFE.sub("-", (text or "").strip().lower()).strip("-")
    return slug[:48] or "macro"


def infer_parent_event_id(ref: dict[str, Any], *, tags: dict[str, Any] | None = None) -> str | None:
    """Stable parent id for long-running macro threads, or None."""
    existing = str(ref.get("parent_event_id") or "").strip()
    if existing:
        return existing

    tag_row = tags if isinstance(tags, dict) else (ref.get("tags") if isinstance(ref.get("tags"), dict) else {})
    topics = {str(t).lower() for t in (tag_row.get("topics") or [])}
    themes = {str(t).lower() for t in (tag_row.get("themes") or [])}
    factors = {str(t).lower() for t in (tag_row.get("factors") or [])}
    pool = topics | themes | factors
    allowed = parent_topics_from_env()
    hit = next((t for t in pool if t in allowed), None)
    if not hit:
        return None

    publish_day = publish_day_from_value(str(ref.get("published_at") or ""))
    year = (publish_day or "")[:4] or "unknown"
    return f"parent:{_slug_part(hit)}:{year}"


def event_parent_id(event: dict[str, Any]) -> str | None:
    """Read parent id from distilled event structured_summary.event_meta."""
    structured = event.get("structured_summary") if isinstance(event.get("structured_summary"), dict) else {}
    meta = structured.get("event_meta") if isinstance(structured.get("event_meta"), dict) else {}
    pid = str(meta.get("parent_event_id") or event.get("parent_event_id") or "").strip()
    return pid or None


def infer_event_kind(ref: dict[str, Any], *, tags: dict[str, Any] | None = None) -> str:
    if str(ref.get("event_kind") or "").strip():
        return str(ref["event_kind"])
    if infer_parent_event_id(ref, tags=tags):
        return "macro"
    sym = str(ref.get("ticker") or "NIFTY").upper()
    if sym in {"NIFTY", "SENSEX", "BANKNIFTY", "NIFTY50"}:
        return "macro"
    return "micro"


def infer_scope(ref: dict[str, Any]) -> str:
    sym = str(ref.get("ticker") or "NIFTY").upper()
    if sym in {"NIFTY", "SENSEX", "BANKNIFTY", "NIFTY50"}:
        return "index"
    return "symbol"


def infer_provenance(ref: dict[str, Any]) -> str:
    return str(ref.get("provenance") or "live").strip() or "live"
