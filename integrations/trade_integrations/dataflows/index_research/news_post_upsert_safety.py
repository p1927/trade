"""Phase 4 — post-upsert 7-day safety scan (merge-on-write safety net)."""

from __future__ import annotations

import logging
import os
from typing import Any

logger = logging.getLogger(__name__)

_DEFAULT_LOOKBACK_DAYS = 7


def post_upsert_safety_enabled() -> bool:
    return os.getenv("HUB_NEWS_POST_UPSERT_SAFETY_SCAN", "1").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def post_upsert_lookback_days() -> int:
    try:
        return max(1, int(os.getenv("HUB_NEWS_POST_UPSERT_LOOKBACK_DAYS", str(_DEFAULT_LOOKBACK_DAYS))))
    except ValueError:
        return _DEFAULT_LOOKBACK_DAYS


def _surviving_event_id(group: list[dict[str, Any]], *, preferred: str) -> str:
    from trade_integrations.hub_storage.news_events_store import get_event

    if preferred and get_event(preferred):
        return preferred
    for row in group:
        rid = str(row.get("canonical_story_id") or row.get("event_id") or "")
        if rid and get_event(rid):
            return rid
    return preferred


def _lookback_since_iso(*, lookback_days: int) -> str:
    from datetime import date, timedelta

    from trade_integrations.dataflows.company_research.market import india_trading_date_iso

    end = date.fromisoformat(india_trading_date_iso()[:10])
    return (end - timedelta(days=max(lookback_days, 1))).isoformat()


def run_post_upsert_safety_scan(
    event_id: str,
    *,
    ticker: str,
    lookback_days: int | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Scan recent events for two-signal duplicates involving ``event_id`` and merge if found."""
    eid = str(event_id or "").strip()
    sym = ticker.strip().upper()
    if not eid or not sym:
        return {"skipped": True, "reason": "missing_event_or_ticker"}

    if not post_upsert_safety_enabled():
        return {"skipped": True, "reason": "disabled"}

    from trade_integrations.hub_storage.news_events_store import (
        distilled_event_to_headline_dict,
        get_event,
        list_events,
    )

    stored = get_event(eid)
    if not stored:
        return {"skipped": True, "reason": "event_not_found", "event_id": eid}

    window_days = lookback_days if lookback_days is not None else post_upsert_lookback_days()
    since = _lookback_since_iso(lookback_days=window_days)
    raw_events = list_events(
        ticker=sym,
        since=since,
        limit=500,
        include_rejected=False,
    )
    records = [distilled_event_to_headline_dict(event) for event in raw_events]
    anchor = distilled_event_to_headline_dict(stored)

    wiki_index: dict[str, Any] = {"by_event_id": {}, "by_slug": {}}
    try:
        from trade_integrations.dataflows.hub_wiki.search_dedup import build_source_event_index

        wiki_index = build_source_event_index()
    except Exception as exc:
        logger.debug("post-upsert safety wiki index skipped: %s", exc)

    from trade_integrations.dataflows.index_research.news_entity_worker import (
        _build_duplicate_group,
        _merge_duplicate_group,
    )

    group = _build_duplicate_group(
        anchor,
        records,
        ticker=sym,
        consumed=set(),
        wiki_index=wiki_index,
    )
    if len(group) < 2:
        return {
            "skipped": False,
            "event_id": eid,
            "groups_merged": 0,
            "rows_removed": 0,
            "lookback_days": window_days,
        }

    consumed: set[str] = set()
    merge_result = _merge_duplicate_group(
        group,
        ticker=sym,
        consumed=consumed,
        dry_run=dry_run,
        reason="post_upsert_safety",
        wiki_index=wiki_index,
        preferred_canon_id=eid,
    )
    canonical_id = _surviving_event_id(group, preferred=eid)
    merged_into = canonical_id if canonical_id != eid else None
    return {
        "skipped": False,
        "event_id": eid,
        "merged_into": merged_into,
        "canonical_event_id": canonical_id,
        "groups_merged": int(merge_result.get("groups_merged") or 0),
        "rows_removed": int(merge_result.get("rows_removed") or 0),
        "wiki_files_removed": int(merge_result.get("wiki_files_removed") or 0),
        "lookback_days": window_days,
        "dry_run": dry_run,
    }
