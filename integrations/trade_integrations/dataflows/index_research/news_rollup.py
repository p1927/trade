"""Parent-topic rollup to reduce distilled event bloat."""

from __future__ import annotations

import uuid
from datetime import date, timedelta
from typing import Any

from trade_integrations.dataflows.index_research.news_parent_events import event_parent_id


def rollup_parent_topic_events(
    *,
    ticker: str = "NIFTY",
    lookback_days: int = 7,
    min_events: int = 5,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Merge 5+ same-parent events in lookback window into one digest event."""
    from trade_integrations.dataflows.index_research.news_distillation import distill_event
    from trade_integrations.dataflows.index_research.news_entity_worker import _record_to_ref
    from trade_integrations.dataflows.index_research.news_impact_engine import ingest_headline_rows
    from trade_integrations.dataflows.index_research.news_dedup import publish_day_from_value
    from trade_integrations.hub_storage.news_events_store import (
        distilled_event_to_headline_dict,
        list_events,
        remove_events,
    )
    from trade_integrations.dataflows.hub_wiki.compile import compile_event_to_wiki, wiki_compile_enabled

    sym = ticker.strip().upper()
    since = (date.today() - timedelta(days=max(lookback_days, 1))).isoformat()
    raw = list_events(ticker=sym, since=since, limit=5000, include_rejected=False)
    records = [distilled_event_to_headline_dict(e) for e in raw]

    by_parent: dict[str, list[dict[str, Any]]] = {}
    for rec in records:
        parent = event_parent_id(rec) or str(
            ((rec.get("structured_summary") or {}).get("event_meta") or {}).get("parent_event_id") or ""
        )
        if not parent:
            continue
        by_parent.setdefault(parent, []).append(rec)

    digests_created = 0
    rows_rolled = 0
    wiki_exports = 0
    for parent_id, group in by_parent.items():
        if len(group) < min_events:
            continue
        refs = [_record_to_ref(r) for r in group]
        prior = max(group, key=lambda r: str(r.get("updated_at") or r.get("first_seen_at") or ""))
        if dry_run:
            digests_created += 1
            rows_rolled += len(group)
            continue

        distilled = distill_event(refs=refs, previous=prior)
        digest_id = f"digest:{parent_id}:{uuid.uuid4().hex[:8]}"
        publish_day = publish_day_from_value(str(prior.get("published_at") or "")) or since
        row = {
            "title": distilled.get("title") or f"Digest: {parent_id}",
            "summary": distilled.get("content") or "",
            "structured_summary": distilled.get("structured_summary") or {},
            "tags": prior.get("tags") or {},
            "sources": [],
            "published_at": publish_day,
        }
        em = (row.get("structured_summary") or {}).get("event_meta") or {}
        em["event_id"] = digest_id
        em["parent_event_id"] = parent_id
        em["distilled_by"] = em.get("distilled_by") or "rollup"
        em["ref_count"] = len(group)
        row["structured_summary"] = {**(row.get("structured_summary") or {}), "event_meta": em}

        ingest_headline_rows([row], ticker=sym, collection_day=publish_day, force_reverify=False)

        drop_ids = {
            str(r.get("canonical_story_id") or r.get("event_id") or "")
            for r in group
            if str(r.get("canonical_story_id") or r.get("event_id") or "")
        }
        remove_events(drop_ids)

        if wiki_compile_enabled():
            from trade_integrations.hub_storage.news_events_store import get_event

            stored = get_event(digest_id)
            if stored:
                result = compile_event_to_wiki(stored, rescan=False)
                if result.get("ok"):
                    wiki_exports += 1

        digests_created += 1
        rows_rolled += len(group)

    wiki_rescan: dict[str, Any] | None = None
    if wiki_exports > 0:
        from trade_integrations.dataflows.hub_wiki.compile import batch_rescan_if_enabled

        wiki_rescan = batch_rescan_if_enabled()

    return {
        "ticker": sym,
        "lookback_days": lookback_days,
        "min_events": min_events,
        "dry_run": dry_run,
        "digests_created": digests_created,
        "rows_rolled": rows_rolled,
        "parent_groups": len(by_parent),
        "wiki_exports": wiki_exports,
        "wiki_rescan": wiki_rescan,
    }
