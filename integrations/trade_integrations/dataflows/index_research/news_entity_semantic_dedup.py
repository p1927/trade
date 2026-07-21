"""Semantic search and clustering for hub news entity maintenance.

Reuses the staging-tier embedding stack (``hub_wiki.embeddings`` + stdlib fallback)
so the maintainer can find and club similar distilled events without new deps.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from trade_integrations.dataflows.index_research.news_dedup import publish_day_from_value
from trade_integrations.dataflows.index_research.news_embedding_cluster import (
    assign_cluster_ids,
    cluster_threshold,
)


def event_text(record: dict[str, Any]) -> str:
    """Normalized title + body for similarity search."""
    title = str(record.get("title") or "")
    body = str(record.get("content_summary") or record.get("content") or record.get("summary") or "")
    return f"{title} {body}".strip()


def _record_as_ref(record: dict[str, Any]) -> dict[str, Any]:
    rid = str(record.get("canonical_story_id") or record.get("event_id") or "")
    return {
        "ref_id": rid,
        "title": record.get("title") or "",
        "summary": record.get("content_summary") or record.get("content") or "",
        "tags": record.get("tags") or {},
        "published_at": record.get("published_at") or "",
    }


def events_are_merge_candidates(
    left: dict[str, Any],
    right: dict[str, Any],
    *,
    ticker: str,
    threshold: float | None = None,
) -> bool:
    """Rule-based match OR embedding/text similarity above cluster threshold."""
    from trade_integrations.dataflows.index_research.news_event_matching import find_matching_event

    ref = _record_as_ref(left)
    if find_matching_event(ref, [right], ticker=ticker):
        return True

    cut = cluster_threshold() if threshold is None else threshold
    try:
        from trade_integrations.dataflows.index_research.news_embedding_cluster import _similarity

        sim = _similarity(event_text(left), event_text(right))
        return sim >= cut
    except Exception:
        from trade_integrations.dataflows.index_research.news_event_matching import summary_similarity

        return summary_similarity(event_text(left), event_text(right)) >= cut


def build_duplicate_groups_semantic(
    records: list[dict[str, Any]],
    *,
    ticker: str,
    consumed: set[str] | None = None,
    threshold: float | None = None,
) -> list[list[dict[str, Any]]]:
    """Group similar events by publish day using embedding-aware clustering."""
    skip = consumed or set()
    available: list[dict[str, Any]] = []
    for record in records:
        rid = str(record.get("canonical_story_id") or record.get("event_id") or "")
        if not rid or rid in skip:
            continue
        available.append(record)

    if len(available) < 2:
        return []

    by_day: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in available:
        day = publish_day_from_value(
            str(record.get("published_at") or record.get("publish_day") or "")
        )
        by_day[day or "unknown"].append(record)

    groups: list[list[dict[str, Any]]] = []
    cut = cluster_threshold() if threshold is None else threshold

    for _day, day_records in by_day.items():
        if len(day_records) < 2:
            continue
        refs = [_record_as_ref(r) for r in day_records]
        assigned = assign_cluster_ids(refs, threshold=cut)
        by_cluster: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for ref, original in zip(assigned, day_records):
            cid = str(ref.get("cluster_id") or ref.get("ref_id") or "")
            by_cluster[cid].append(original)

        for cluster in by_cluster.values():
            if len(cluster) < 2:
                continue
            anchor = cluster[0]
            mergeable = [anchor]
            for other in cluster[1:]:
                if events_are_merge_candidates(anchor, other, ticker=ticker, threshold=cut):
                    mergeable.append(other)
            if len(mergeable) >= 2:
                groups.append(mergeable)

    return groups
