"""Prediction attribution visibility — soft-create events hidden until corroborated."""

from __future__ import annotations

import os
from typing import Any

from trade_integrations.dataflows.index_research.hub_news_pipeline.step_08_temporal_attribution import (
    enriched_prediction_value_score,
    has_cause_indicators,
    strip_article_opinions,
)


def enriched_single_ref_min_score() -> float:
    try:
        return max(0.0, min(1.0, float(os.getenv("HUB_NEWS_ENRICHED_SINGLE_REF_MIN_SCORE", "0.72"))))
    except ValueError:
        return 0.72


def event_ref_count(record: dict[str, Any]) -> int:
    """Reference count for an event/headline dict."""
    em = ((record.get("structured_summary") or {}).get("event_meta") or {})
    if em.get("ref_count") is not None:
        try:
            return max(0, int(em.get("ref_count")))
        except (TypeError, ValueError):
            pass
    refs = record.get("references") or []
    if isinstance(refs, list) and refs:
        return len(refs)
    sources = record.get("sources") or []
    if isinstance(sources, list) and sources:
        return len(sources)
    return 1


def visible_for_prediction_attribution(record: dict[str, Any]) -> bool:
    """Soft-create policy: hide single-ref SSOT events unless enriched + corroborated."""
    if str(record.get("provenance") or "") == "staging":
        return True
    if event_ref_count(record) >= 2:
        return True
    score = enriched_prediction_value_score(record)
    if score >= enriched_single_ref_min_score() and has_cause_indicators(record):
        return True
    return False


def filter_prediction_attribution_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [strip_article_opinions(row) for row in items if visible_for_prediction_attribution(row)]
