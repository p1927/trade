"""Match incoming refs to existing hub news events — rule-based, no ML deps.

Similarity threshold defaults to 0.72 via ``HUB_NEWS_DEDUP_SUMMARY_THRESHOLD``.
Legacy ``HUB_NEWS_MATCH_THRESHOLD`` is honored when set.
"""

from __future__ import annotations

import os
import re
from difflib import SequenceMatcher
from typing import Any

from trade_integrations.dataflows.index_research.news_dedup import (
    publish_day_from_value,
    semantic_cluster_key,
)
from trade_integrations.dataflows.index_research.news_parent_events import (
    event_parent_id,
    infer_parent_event_id,
)
from trade_integrations.dataflows.index_research.news_tags import tags_from_dict

_BULLISH = frozenset({"rally", "recovery", "record_high"})
_BEARISH = frozenset({"crash", "selloff", "record_low"})
_INDEX_SYMBOLS = frozenset({"NIFTY", "SENSEX", "BANKNIFTY", "NIFTYMID", "NIFTY50"})

_EARNINGS_TICKER = re.compile(r"\(([A-Z0-9-]+)\.(?:NS|BO|BL)\)", re.IGNORECASE)
_EARNINGS_QUARTER = re.compile(r"\bQ([1-4])\s+(\d{2}/\d{2})\b", re.IGNORECASE)
_TRANSCRIPT = re.compile(r"\bearnings call transcript\b", re.IGNORECASE)


def match_threshold() -> float:
    """Configured summary-similarity cutoff for entity merge decisions."""
    legacy = os.getenv("HUB_NEWS_MATCH_THRESHOLD", "").strip()
    if legacy:
        try:
            return float(legacy)
        except ValueError:
            pass
    try:
        return float(os.getenv("HUB_NEWS_DEDUP_SUMMARY_THRESHOLD", "0.72"))
    except ValueError:
        return 0.72


def _normalize_text(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip().lower())


def enhanced_summary_similarity(a: str, b: str) -> float:
    """Text similarity boosted by embedding cosine when available."""
    base = summary_similarity(a, b)
    cut = match_threshold()
    if base >= cut:
        return base
    try:
        from trade_integrations.dataflows.index_research.news_embedding_cluster import _similarity

        return max(base, float(_similarity(a, b)))
    except Exception:
        return base


def summary_similarity(a: str, b: str) -> float:
    """Compare two summaries; stdlib only."""
    na = _normalize_text(a)
    nb = _normalize_text(b)
    if not na or not nb:
        return 0.0
    if na == nb:
        return 1.0
    ratio = SequenceMatcher(None, na, nb).ratio()
    if len(na) < 40 or len(nb) < 40:
        ta = set(na.split())
        tb = set(nb.split())
        if ta and tb:
            jaccard = len(ta & tb) / len(ta | tb)
            ratio = max(ratio, jaccard)
    return float(ratio)


def title_similarity(a: str, b: str) -> float:
    """Compare headlines for same-day event matching."""
    return summary_similarity(a, b)


def _specific_symbols(tags: dict[str, Any], title: str = "") -> set[str]:
    """Company-level symbols mentioned (excludes broad index names)."""
    out = {str(s).upper() for s in (tags.get("symbols") or []) if str(s).strip()}
    for match in _EARNINGS_TICKER.finditer(title or ""):
        out.add(match.group(1).upper())
    return {s for s in out if s not in _INDEX_SYMBOLS}


def _entities_compatible(ref_symbols: set[str], event_symbols: set[str]) -> bool:
    """Block merges across different company-specific stories."""
    if not ref_symbols or not event_symbols:
        return True
    return bool(ref_symbols & event_symbols)


def earnings_transcript_key(title: str) -> str | None:
    """Stable key for earnings transcript dedup: symbol + quarter."""
    if not _TRANSCRIPT.search(title or ""):
        return None
    ticker_match = _EARNINGS_TICKER.search(title or "")
    quarter_match = _EARNINGS_QUARTER.search(title or "")
    if not ticker_match:
        return None
    quarter = quarter_match.group(0).upper().replace(" ", "") if quarter_match else "unknown"
    return f"earn:{ticker_match.group(1).upper()}:{quarter}"


def _earnings_transcripts_compatible(ref_title: str, event_title: str) -> bool:
    ref_key = earnings_transcript_key(ref_title)
    event_key = earnings_transcript_key(event_title)
    if ref_key is None and event_key is None:
        return True
    if ref_key is None or event_key is None:
        return False
    return ref_key == event_key


def _direction_from_tags(tags: dict[str, Any]) -> str:
    themes = list(tags.get("themes") or [])
    bulls = [t for t in themes if t in _BULLISH]
    bears = [t for t in themes if t in _BEARISH]
    if bulls and bears:
        return "mixed"
    if bears:
        return "bearish"
    if bulls:
        return "bullish"
    if "flat" in themes:
        return "flat"
    return "neutral"


def _directions_conflict(a: str, b: str) -> bool:
    if a == b or "mixed" in (a, b) or "neutral" in (a, b):
        return False
    if a in {"bullish", "bearish"} and b in {"bullish", "bearish"}:
        return a != b
    return False


def _topics_overlap(a: dict[str, Any], b: dict[str, Any]) -> bool:
    ta = set(a.get("topics") or [])
    tb = set(b.get("topics") or [])
    if not ta or not tb:
        return True
    return bool(ta & tb)


def event_bucket_key(row: dict[str, Any], *, ticker: str = "NIFTY") -> str:
    return semantic_cluster_key(row, ticker=ticker)


def find_matching_event(
    ref: dict[str, Any],
    events: list[dict[str, Any]],
    *,
    ticker: str = "NIFTY",
    threshold: float | None = None,
) -> dict[str, Any] | None:
    """Return best matching hub event for a staging ref, or None."""
    cut = threshold if threshold is not None else match_threshold()
    ref_day = publish_day_from_value(str(ref.get("published_at") or ""))
    ref_tags = tags_from_dict(ref.get("tags")).to_dict() if ref.get("tags") else {}
    if not ref_tags.get("topics"):
        from trade_integrations.dataflows.index_research.news_tags import build_article_tags

        ref_tags = build_article_tags(
            str(ref.get("title") or ""),
            str(ref.get("summary") or ""),
            ticker=ticker,
            published_at=str(ref.get("published_at") or ""),
        ).to_dict()
        ref["tags"] = ref_tags

    ref_dir = _direction_from_tags(ref_tags)
    ref_text = f"{ref.get('title') or ''} {ref.get('summary') or ''}"
    ref_bucket = event_bucket_key(ref, ticker=ticker)
    ref_title = str(ref.get("title") or "")
    ref_symbols = _specific_symbols(ref_tags, ref_title)
    ref_parent = infer_parent_event_id(ref, tags=ref_tags)

    best: dict[str, Any] | None = None
    best_score = 0.0

    for event in events:
        event_day = publish_day_from_value(str(event.get("published_at") or ""))
        event_parent = event_parent_id(event)
        same_parent_thread = bool(ref_parent and event_parent and ref_parent == event_parent)
        if ref_day and event_day and ref_day != event_day and not same_parent_thread:
            continue

        event_tags = tags_from_dict(event.get("tags")).to_dict()
        if not _topics_overlap(ref_tags, event_tags):
            continue
        if _directions_conflict(ref_dir, _direction_from_tags(event_tags)):
            continue

        event_title = str(event.get("title") or "")
        if not _earnings_transcripts_compatible(ref_title, event_title):
            continue

        event_symbols = _specific_symbols(event_tags, event_title)
        if not _entities_compatible(ref_symbols, event_symbols):
            continue

        event_text = " ".join(
            part
            for part in (
                event_title,
                str(event.get("content_summary") or ""),
            )
            if part
        )
        sim = enhanced_summary_similarity(ref_text, event_text)
        title_sim = title_similarity(ref_title, event_title)
        event_row = {
            "title": event.get("title"),
            "summary": event.get("content_summary"),
            "published_at": event.get("published_at"),
            "tags": event_tags,
        }
        event_bucket = event_bucket_key(event_row, ticker=ticker) if ref_bucket else ""
        bucket_match = bool(ref_bucket and event_bucket == ref_bucket)
        if same_parent_thread and sim >= cut - 0.15:
            sim = min(1.0, sim + 0.1)
        if bucket_match and sim >= cut - 0.1:
            sim = min(1.0, sim + 0.05)
            if title_sim < 0.20 and ref_symbols and event_symbols and not (ref_symbols & event_symbols):
                continue

        if sim >= cut and sim > best_score:
            best_score = sim
            best = event

    return best
