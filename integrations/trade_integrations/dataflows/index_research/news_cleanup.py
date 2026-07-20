"""Scheduled cleanup for hub news staging and SSOT."""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any


def discard_stale_pending(*, ticker: str = "NIFTY", days: int = 14) -> dict[str, Any]:
    """Soft-discard queued refs older than ``days`` without processing."""
    from trade_integrations.hub_storage.news_staging_store import (
        list_pending_refs,
        mark_ref_discarded,
    )

    sym = ticker.strip().upper()
    cutoff = date.today() - timedelta(days=max(days, 1))
    discarded = 0
    for ref in list_pending_refs(ticker=sym, limit=10_000):
        pub = str(ref.get("published_at") or ref.get("created_at") or "")[:10]
        if not pub:
            continue
        try:
            pub_day = date.fromisoformat(pub[:10])
        except ValueError:
            continue
        if pub_day > cutoff:
            continue
        ref_id = str(ref.get("ref_id") or "")
        if not ref_id:
            continue
        mark_ref_discarded(
            ref_id,
            reason=f"stale pending >{days}d",
            restore_payload=dict(ref),
            source_kind="cleanup",
        )
        discarded += 1
    return {"ticker": sym, "discarded": discarded, "days": days}


def archive_rejected_events(*, ticker: str = "NIFTY", days: int = 30) -> dict[str, Any]:
    """Move old rejected events to discarded ledger and remove from SSOT."""
    from trade_integrations.hub_storage.news_events_store import (
        list_events,
        remove_events,
    )
    from trade_integrations.hub_storage.news_staging_store import append_discarded_record
    from trade_integrations.dataflows.hub_wiki.compile import remove_event_wiki_files

    sym = ticker.strip().upper()
    cutoff = (date.today() - timedelta(days=max(days, 1))).isoformat()
    archived = 0
    events = list_events(ticker=sym, limit=5000, include_rejected=True, status="rejected")
    drop_ids: list[str] = []
    for event in events:
        pub = str(event.get("published_at") or event.get("publish_day") or "")[:10]
        if pub and pub > cutoff:
            continue
        event_id = str(event.get("event_id") or "")
        if not event_id:
            continue
        append_discarded_record(
            source_kind="cleanup",
            ticker=sym,
            title=str(event.get("title") or ""),
            url="",
            reason="rejected event archived",
            event_id=event_id,
            restore_payload=dict(event),
        )
        remove_event_wiki_files(event)
        drop_ids.append(event_id)
        archived += 1
    removed = remove_events(drop_ids) if drop_ids else 0
    return {"ticker": sym, "archived": archived, "removed": removed}


def cleanup_hub_news(*, ticker: str = "NIFTY") -> dict[str, Any]:
    """Orchestrate discard ledger purge and maintenance passes."""
    from trade_integrations.hub_storage.news_staging_store import purge_expired_discarded

    sym = ticker.strip().upper()
    purged = purge_expired_discarded()
    stale = discard_stale_pending(ticker=sym)
    rejected = archive_rejected_events(ticker=sym)
    return {
        "ticker": sym,
        "purged_discarded": purged,
        "stale_pending": stale,
        "rejected_archived": rejected,
    }
