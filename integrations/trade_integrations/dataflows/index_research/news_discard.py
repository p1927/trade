"""Manual discard, discard-similar, and undo for hub news."""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any


def _tag_overlap(anchor: dict[str, Any], candidate: dict[str, Any]) -> bool:
    def _tags(row: dict[str, Any]) -> tuple[set[str], set[str]]:
        tags = row.get("tags") if isinstance(row.get("tags"), dict) else {}
        topics = {str(t).lower() for t in (tags.get("topics") or [])}
        factors = {str(t).lower() for t in (tags.get("factors") or [])}
        em = ((row.get("structured_summary") or {}).get("event_meta") or {})
        if isinstance(em, dict):
            consensus = em.get("consensus") or {}
            if isinstance(consensus, dict):
                topics |= {str(t).lower() for t in (consensus.get("topics") or [])}
                factors |= {str(t).lower() for t in (consensus.get("factors") or [])}
        return topics, factors

    a_topics, a_factors = _tags(anchor)
    c_topics, c_factors = _tags(candidate)
    if not a_topics and not a_factors:
        return True
    return bool((a_topics & c_topics) or (a_factors & c_factors))


def _ref_text(row: dict[str, Any]) -> str:
    return f"{row.get('title') or ''} {row.get('summary') or row.get('content_summary') or row.get('content') or ''}".strip()


def _similarity(text_a: str, text_b: str) -> float:
    from trade_integrations.dataflows.index_research.news_embedding_cluster import _similarity as _sim

    return _sim(text_a, text_b)


def find_similar_candidates(
    anchor: dict[str, Any],
    *,
    ticker: str = "NIFTY",
    lookback_days: int = 7,
    threshold: float | None = None,
) -> list[dict[str, Any]]:
    """Semantic cluster + topic/factor overlap candidates."""
    from trade_integrations.dataflows.index_research.news_embedding_cluster import cluster_threshold
    from trade_integrations.hub_storage.news_events_store import (
        distilled_event_to_headline_dict,
        list_events,
    )
    from trade_integrations.hub_storage.news_staging_store import list_pending_refs

    sym = ticker.strip().upper()
    cut = cluster_threshold() if threshold is None else threshold
    anchor_text = _ref_text(anchor)
    anchor_id = str(
        anchor.get("ref_id")
        or anchor.get("event_id")
        or anchor.get("canonical_story_id")
        or anchor.get("id")
        or ""
    )
    since = (date.today() - timedelta(days=max(lookback_days, 1))).isoformat()

    pool: list[dict[str, Any]] = list(list_pending_refs(ticker=sym, limit=500))
    events = list_events(ticker=sym, since=since, limit=500)
    pool.extend(distilled_event_to_headline_dict(e) for e in events)

    matches: list[dict[str, Any]] = []
    for cand in pool:
        cid = str(
            cand.get("ref_id")
            or cand.get("event_id")
            or cand.get("canonical_story_id")
            or cand.get("id")
            or ""
        )
        if cid and cid == anchor_id:
            continue
        sim = _similarity(anchor_text, _ref_text(cand))
        if sim < cut:
            continue
        if not _tag_overlap(anchor, cand):
            continue
        cand_copy = dict(cand)
        cand_copy["_similarity"] = round(sim, 4)
        matches.append(cand_copy)
    matches.sort(key=lambda r: float(r.get("_similarity") or 0), reverse=True)
    return matches


def discard_news_item(
    item_id: str,
    *,
    ticker: str = "NIFTY",
    source_kind: str = "staging",
    reason: str = "manual discard",
) -> dict[str, Any]:
    """Discard one staging ref or distilled event."""
    from trade_integrations.hub_storage.news_events_store import get_event, remove_events
    from trade_integrations.hub_storage.news_staging_store import (
        append_discarded_record,
        list_pending_refs,
        mark_ref_discarded,
    )
    from trade_integrations.dataflows.hub_wiki.compile import remove_event_wiki_files

    sym = ticker.strip().upper()
    iid = str(item_id or "").strip()
    if not iid:
        raise ValueError("item_id required")

    if source_kind == "staging" or iid.startswith("ref:"):
        for ref in list_pending_refs(ticker=sym, limit=10_000):
            if str(ref.get("ref_id") or "") == iid:
                row = mark_ref_discarded(
                    iid,
                    reason=reason,
                    restore_payload=dict(ref),
                    source_kind="manual",
                )
                return {"discarded": [row], "count": 1}
        raise ValueError(f"staging ref not found: {iid}")

    event = get_event(iid)
    if not event:
        raise ValueError(f"event not found: {iid}")
    row = append_discarded_record(
        source_kind="manual",
        ticker=sym,
        title=str(event.get("title") or ""),
        url="",
        reason=reason,
        event_id=iid,
        ref_id="",
        restore_payload=dict(event),
    )
    remove_event_wiki_files(event)
    remove_events([iid])
    return {"discarded": [row], "count": 1}


def discard_similar_items(
    anchor: dict[str, Any],
    *,
    ticker: str = "NIFTY",
    reason: str = "manual discard similar",
    threshold: float | None = None,
) -> dict[str, Any]:
    """Discard anchor + semantically similar items with topic/factor overlap."""
    sym = ticker.strip().upper()
    anchor_id = str(
        anchor.get("ref_id")
        or anchor.get("event_id")
        or anchor.get("canonical_story_id")
        or anchor.get("id")
        or ""
    )
    provenance = str(anchor.get("provenance") or "")
    source_kind = "staging" if provenance == "staging" or anchor_id.startswith("ref:") else "distilled"

    discarded: list[dict[str, Any]] = []
    skipped: list[str] = []

    try:
        if anchor_id:
            result = discard_news_item(
                anchor_id,
                ticker=sym,
                source_kind=source_kind,
                reason=reason,
            )
            discarded.extend(result.get("discarded") or [])
    except ValueError as exc:
        skipped.append(str(exc))

    for cand in find_similar_candidates(anchor, ticker=sym, threshold=threshold):
        cid = str(
            cand.get("ref_id")
            or cand.get("event_id")
            or cand.get("canonical_story_id")
            or cand.get("id")
            or ""
        )
        if not cid:
            continue
        sk = "staging" if str(cand.get("provenance") or "") == "staging" or cid.startswith("ref:") else "distilled"
        try:
            result = discard_news_item(cid, ticker=sym, source_kind=sk, reason=reason)
            discarded.extend(result.get("discarded") or [])
        except ValueError as exc:
            skipped.append(f"{cid}: {exc}")

    return {
        "discarded": discarded,
        "discarded_count": len(discarded),
        "skipped": skipped,
        "discard_ids": [str(r.get("discard_id") or "") for r in discarded if r.get("discard_id")],
    }


def undo_discard(discard_id: str) -> dict[str, Any]:
    from trade_integrations.hub_storage.news_staging_store import restore_discarded

    return restore_discarded(discard_id)


def list_discarded(*, ticker: str = "NIFTY", limit: int = 100) -> list[dict[str, Any]]:
    from trade_integrations.hub_storage.news_staging_store import list_discarded_refs

    return list_discarded_refs(ticker=ticker, limit=limit)


def preview_discard_similar(
    anchor: dict[str, Any],
    *,
    ticker: str = "NIFTY",
    threshold: float | None = None,
) -> dict[str, Any]:
    """Dry-run count for discard-similar UI confirmation."""
    matches = find_similar_candidates(anchor, ticker=ticker, threshold=threshold)
    return {
        "similar_count": len(matches),
        "items": [
            {
                "id": m.get("ref_id") or m.get("event_id") or m.get("canonical_story_id"),
                "title": m.get("title"),
                "similarity": m.get("_similarity"),
            }
            for m in matches[:20]
        ],
    }
