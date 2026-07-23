"""Two-signal club merge for hub news compaction (similarity + wiki or parent)."""

from __future__ import annotations

import logging
from collections import defaultdict
from typing import Any

logger = logging.getLogger(__name__)

from trade_integrations.dataflows.index_research.news_dedup import publish_day_from_value
from trade_integrations.dataflows.index_research.news_entity_semantic_dedup import (
    events_are_merge_candidates,
)
from trade_integrations.dataflows.index_research.news_embedding_cluster import (
    assign_cluster_ids,
    cluster_threshold,
)
from trade_integrations.dataflows.index_research.news_parent_events import event_parent_id


def shared_parent_event_id(left: dict[str, Any], right: dict[str, Any]) -> bool:
    """True when both events share a non-empty parent_event_id."""
    lp = event_parent_id(left)
    rp = event_parent_id(right)
    return bool(lp and rp and lp == rp)


def wiki_link_confirms_pair(
    left: dict[str, Any],
    right: dict[str, Any],
    *,
    ticker: str,
    wiki_index: dict[str, Any] | None = None,
) -> bool:
    """Second signal: wiki search links both records to the same event id."""
    left_id = str(left.get("canonical_story_id") or left.get("event_id") or "")
    right_id = str(right.get("canonical_story_id") or right.get("event_id") or "")
    if not left_id or not right_id or left_id == right_id:
        return False

    try:
        from trade_integrations.dataflows.hub_wiki.search_dedup import find_wiki_match_for_record
        from trade_integrations.hub_storage.news_pipeline_config import load_news_pipeline_config

        pipe_cfg = load_news_pipeline_config()
        if not pipe_cfg.wiki_search_enabled:
            return False

        index = wiki_index or {}
        _ = index  # reserved for future index-aware fast path

        def _ref(record: dict[str, Any]) -> dict[str, Any]:
            return {
                "title": record.get("title") or "",
                "summary": record.get("content_summary") or record.get("content") or "",
                "published_at": record.get("published_at") or record.get("publish_day") or "",
                "tags": record.get("tags") or {},
                "canonical_story_id": record.get("canonical_story_id") or record.get("event_id"),
            }

        left_hit = find_wiki_match_for_record(
            _ref(left),
            ticker=ticker,
            top_k=pipe_cfg.wiki_search_top_k,
            min_score=pipe_cfg.wiki_search_min_score,
        )
        right_hit = find_wiki_match_for_record(
            _ref(right),
            ticker=ticker,
            top_k=pipe_cfg.wiki_search_top_k,
            min_score=pipe_cfg.wiki_search_min_score,
        )
        if not left_hit and not right_hit:
            return False

        targets: set[str] = set()
        for hit in (left_hit, right_hit):
            if hit:
                tid = str(hit.get("event_id") or "").strip()
                if tid:
                    targets.add(tid)

        if len(targets) == 1:
            target = next(iter(targets))
            return target in {left_id, right_id}

        if left_hit and str(left_hit.get("event_id") or "") in {left_id, right_id}:
            if right_hit and str(right_hit.get("event_id") or "") in {left_id, right_id}:
                return str(left_hit.get("event_id") or "") == str(right_hit.get("event_id") or "")
        if left_hit and str(left_hit.get("event_id") or "") == right_id:
            return True
        if right_hit and str(right_hit.get("event_id") or "") == left_id:
            return True
    except Exception as exc:
        logger.debug(
            "wiki_link_confirms_pair lookup failed for %s vs %s: %s",
            left_id,
            right_id,
            exc,
        )
        return False
    return False


def two_signal_merge_eligible(
    left: dict[str, Any],
    right: dict[str, Any],
    *,
    ticker: str,
    threshold: float | None = None,
    wiki_index: dict[str, Any] | None = None,
) -> tuple[bool, str]:
    """Require embedding/rule similarity AND (shared parent OR wiki link)."""
    if not events_are_merge_candidates(left, right, ticker=ticker, threshold=threshold):
        return False, ""
    if shared_parent_event_id(left, right):
        return True, "shared_parent"
    if wiki_link_confirms_pair(left, right, ticker=ticker, wiki_index=wiki_index):
        return True, "wiki_link"
    return False, ""


def _record_as_ref(record: dict[str, Any]) -> dict[str, Any]:
    rid = str(record.get("canonical_story_id") or record.get("event_id") or "")
    return {
        "ref_id": rid,
        "title": record.get("title") or "",
        "summary": record.get("content_summary") or record.get("content") or "",
        "tags": record.get("tags") or {},
        "published_at": record.get("published_at") or "",
    }


def build_duplicate_groups_two_signal(
    records: list[dict[str, Any]],
    *,
    ticker: str,
    consumed: set[str] | None = None,
    threshold: float | None = None,
    wiki_index: dict[str, Any] | None = None,
) -> list[list[dict[str, Any]]]:
    """Cluster similar events but keep only pairs with a second merge signal."""
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
                ok, _reason = two_signal_merge_eligible(
                    anchor,
                    other,
                    ticker=ticker,
                    threshold=cut,
                    wiki_index=wiki_index,
                )
                if ok:
                    mergeable.append(other)
            if len(mergeable) >= 2:
                groups.append(mergeable)

    return groups
