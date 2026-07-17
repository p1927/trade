"""Process staging refs into distilled hub news events."""

from __future__ import annotations

import logging
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from trade_integrations.context.hub import get_hub_dir
from trade_integrations.dataflows.index_research.news_dedup import (
    canonical_story_id,
    publish_day_from_value,
    story_key_from_row,
)
from trade_integrations.dataflows.index_research.news_distillation import (
    distill_event,
    is_distillation_leak,
    strip_minimax_thinking,
)
from trade_integrations.dataflows.index_research.news_event_matching import find_matching_event
from trade_integrations.dataflows.index_research.news_tags import build_article_tags
from trade_integrations.hub_storage.news_staging_store import (
    is_entity_pipeline_enabled,
    list_pending_refs,
    mark_ref_merged,
    require_minimax_for_distillation,
)
from trade_integrations.hub_storage.news_merge_ledger import append_merge_event
from trade_integrations.hub_storage.verified_news_store import get_verified_record, list_verified_records

logger = logging.getLogger(__name__)

_worker_lock = threading.Lock()
_last_run_at: float = 0.0
_WORKER_LAST_REL = Path("_data") / "news_staging" / "worker_last.json"


def _worker_last_path() -> Path:
    return get_hub_dir() / _WORKER_LAST_REL


def _write_worker_last(summary: dict[str, Any]) -> None:
    import json

    path = _worker_last_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        **summary,
        "finished_at": datetime.now(timezone.utc).isoformat(),
    }
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def load_worker_last_summary() -> dict[str, Any] | None:
    import json

    path = _worker_last_path()
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def _merge_sources(existing: list[dict[str, Any]], incoming: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = list(existing or [])
    seen = {f"{s.get('vendor')}|{s.get('url')}" for s in out if isinstance(s, dict)}
    for src in incoming or []:
        if not isinstance(src, dict):
            continue
        key = f"{src.get('vendor')}|{src.get('url')}"
        if key in seen:
            continue
        seen.add(key)
        out.append(src)
    return out


def _ref_to_process_row(ref: dict[str, Any], *, event_id: str | None = None) -> dict[str, Any]:
    tags = ref.get("tags")
    if not isinstance(tags, dict) or not tags.get("topics"):
        tags = build_article_tags(
            str(ref.get("title") or ""),
            str(ref.get("summary") or ""),
            ticker=str(ref.get("ticker") or "NIFTY"),
            published_at=str(ref.get("published_at") or ""),
        ).to_dict()
    row = {
        "title": ref.get("title") or "",
        "summary": ref.get("summary") or "",
        "url": ref.get("url") or "",
        "source": ref.get("source") or "unknown",
        "published_at": ref.get("published_at") or "",
        "sources": ref.get("sources") or [],
        "tags": tags,
    }
    if event_id:
        row["canonical_story_id"] = event_id
    return row


def _apply_distilled_to_row(row: dict[str, Any], distilled: dict[str, Any]) -> dict[str, Any]:
    row = dict(row)
    row["title"] = distilled.get("title") or row.get("title")
    row["summary"] = distilled.get("content") or row.get("summary")
    row["structured_summary"] = distilled.get("structured_summary") or {}
    return row


def _log_merge(
    *,
    ticker: str,
    canonical_story_id: str,
    row: dict[str, Any],
    merged_story_ids: list[str],
    reason: str,
) -> None:
    em = ((row.get("structured_summary") or {}).get("event_meta") or {})
    try:
        append_merge_event(
            ticker=ticker,
            event_id=str(em.get("event_id") or canonical_story_id),
            canonical_story_id=canonical_story_id,
            merged_story_ids=merged_story_ids,
            ref_count=int(em.get("ref_count") or len(em.get("references") or []) or 1),
            reason=reason,
            title=str(row.get("title") or ""),
        )
    except Exception as exc:
        logger.debug("merge ledger append failed: %s", exc)


def process_staging_ref(
    ref: dict[str, Any],
    *,
    ticker: str | None = None,
) -> dict[str, Any]:
    """Match ref to hub event, distill, verify, and upsert."""
    from trade_integrations.dataflows.index_research.news_impact_engine import ingest_headline_rows

    sym = (ticker or ref.get("ticker") or "NIFTY").strip().upper()
    publish_day = publish_day_from_value(str(ref.get("published_at") or ""))

    candidates = list_verified_records(
        ticker=sym,
        publish_day=publish_day or None,
        since=publish_day or None,
        limit=80,
        include_rejected=False,
    )
    matched = find_matching_event(ref, candidates, ticker=sym)
    event_id = str(matched.get("canonical_story_id") or "") if matched else ""

    if not matched:
        url_id = canonical_story_id(str(ref.get("title") or ""), str(ref.get("url") or ""))
        if url_id and get_verified_record(url_id):
            mark_ref_merged(ref["ref_id"], url_id)
            return {"action": "skip_duplicate_url", "event_id": url_id}

    if matched:
        refs_for_distill = [ref]
        prior_meta = ((matched.get("structured_summary") or {}).get("event_meta") or {})
        prior_refs = list(prior_meta.get("references") or [])
        merged_sources = _merge_sources(matched.get("sources") or [], ref.get("sources") or [])
        row = _ref_to_process_row(ref, event_id=event_id)
        row["sources"] = merged_sources
        if prior_refs:
            refs_for_distill = prior_refs + [_ref_to_process_row(ref)]
        distilled = distill_event(refs=refs_for_distill, previous=matched)
        row = _apply_distilled_to_row(row, distilled)
        action = "update"
    else:
        event_id = canonical_story_id(str(ref.get("title") or ""), str(ref.get("url") or ""))
        row = _ref_to_process_row(ref, event_id=event_id)
        distilled = distill_event(refs=[ref], previous=None)
        row = _apply_distilled_to_row(row, distilled)
        action = "create"

    stats = ingest_headline_rows(
        [row],
        ticker=sym,
        collection_day=publish_day or None,
        force_reverify=action == "update",
    )
    if action == "update":
        _log_merge(
            ticker=sym,
            canonical_story_id=event_id,
            row=row,
            merged_story_ids=[],
            reason="staging_update",
        )
    mark_ref_merged(ref["ref_id"], event_id)
    return {"action": action, "event_id": event_id, "stats": stats}


def process_staging_batch(
    *,
    ticker: str | None = None,
    limit: int = 20,
    force_reverify: bool = False,
) -> dict[str, Any]:
    """Process up to ``limit`` queued staging refs."""
    if not is_entity_pipeline_enabled():
        return {"processed": 0, "skipped": True}

    require_minimax_for_distillation()

    pending = list_pending_refs(ticker=ticker, limit=limit)
    summary: dict[str, Any] = {
        "processed": 0,
        "created": 0,
        "updated": 0,
        "skipped": 0,
        "errors": 0,
    }
    for ref in pending:
        try:
            result = process_staging_ref(ref, ticker=ticker)
            summary["processed"] += 1
            action = result.get("action")
            if action == "create":
                summary["created"] += 1
            elif action == "update":
                summary["updated"] += 1
            elif action == "skip_duplicate_url":
                summary["skipped"] += 1
        except Exception as exc:
            summary["errors"] += 1
            logger.warning("staging ref %s failed: %s", ref.get("ref_id"), exc)
    if summary.get("processed"):
        try:
            _write_worker_last({"ticker": ticker, **summary})
        except Exception as exc:
            logger.debug("worker last summary write failed: %s", exc)
    return summary


def schedule_staging_processing(*, ticker: str | None = None, limit: int = 15) -> None:
    """Fire-and-forget background batch (debounced)."""
    import time

    global _last_run_at
    if not is_entity_pipeline_enabled():
        return

    now = time.time()
    if now - _last_run_at < 30:
        return

    def _run() -> None:
        global _last_run_at
        with _worker_lock:
            _last_run_at = time.time()
            try:
                process_staging_batch(ticker=ticker, limit=limit)
            except Exception as exc:
                logger.debug("background staging batch failed: %s", exc)

    threading.Thread(target=_run, daemon=True, name="hub-news-entity-worker").start()


def run_hub_news_entity_job(config: dict[str, Any] | None = None) -> dict[str, Any]:
    """Daily compaction: drain staging queue and process pending refs."""
    cfg = config or {}
    ticker = str(cfg.get("ticker") or "NIFTY")
    limit = int(cfg.get("batch_size") or 200)
    lookback = int(cfg.get("lookback_days") or 365)
    staging = process_staging_batch(ticker=ticker, limit=limit, force_reverify=False)
    repair = repair_leaked_distilled_summaries(ticker=ticker)
    backfill = backfill_distilled_event_metadata(ticker=ticker)
    compact = compact_duplicate_events(ticker=ticker, lookback_days=lookback)
    return {"staging": staging, "repair": repair, "backfill": backfill, "compact": compact}


def _record_to_ref(rec: dict[str, Any]) -> dict[str, Any]:
    summary = strip_minimax_thinking(str(rec.get("content_summary") or ""))
    return {
        "title": rec.get("title") or "",
        "summary": summary,
        "url": rec.get("url") or "",
        "source": rec.get("source") or "hub",
        "published_at": rec.get("published_at") or "",
        "sources": rec.get("sources") or [],
        "tags": rec.get("tags") or {},
    }


def _build_duplicate_group(
    anchor: dict[str, Any],
    records: list[dict[str, Any]],
    *,
    ticker: str,
    consumed: set[str],
) -> list[dict[str, Any]]:
    """Collect all records transitively matching the anchor event."""
    anchor_id = str(anchor.get("canonical_story_id") or "")
    group = [anchor]
    group_ids = {anchor_id}
    changed = True
    while changed:
        changed = False
        for other in records:
            oid = str(other.get("canonical_story_id") or "")
            if not oid or oid in consumed or oid in group_ids:
                continue
            ref = _record_to_ref(other)
            ref["tags"] = other.get("tags") or ref.get("tags")
            if any(find_matching_event(ref, [member], ticker=ticker) for member in group):
                group.append(other)
                group_ids.add(oid)
                changed = True
    return group


def repair_leaked_distilled_summaries(*, ticker: str = "NIFTY") -> dict[str, Any]:
    """Re-distill hub rows whose summaries contain MiniMax thinking artifacts."""
    from trade_integrations.dataflows.index_research.news_impact_engine import ingest_headline_rows

    require_minimax_for_distillation()
    sym = ticker.strip().upper()
    records = list_verified_records(ticker=sym, limit=5000, include_rejected=True)
    repaired = 0
    errors = 0

    for rec in records:
        summary = str(rec.get("content_summary") or "")
        if not is_distillation_leak(summary):
            continue
        story_id = str(rec.get("canonical_story_id") or "")
        if not story_id:
            continue
        ss = rec.get("structured_summary") or {}
        em = (ss.get("event_meta") if isinstance(ss, dict) else {}) or {}
        refs = list(em.get("references") or [])
        if not refs:
            refs = [_record_to_ref(rec)]
        try:
            clean_previous = dict(rec)
            clean_previous["content_summary"] = ""
            distilled = distill_event(refs=refs, previous=clean_previous)
            row = _ref_to_process_row(_record_to_ref(rec), event_id=story_id)
            row = _apply_distilled_to_row(row, distilled)
            if is_distillation_leak(str(row.get("summary") or "")):
                raise RuntimeError("distillation still leaked thinking text")
            publish_day = publish_day_from_value(str(rec.get("published_at") or ""))
            ingest_headline_rows(
                [row],
                ticker=sym,
                collection_day=publish_day or None,
                force_reverify=True,
            )
            repaired += 1
        except Exception as exc:
            errors += 1
            logger.warning("failed to repair leaked summary for %s: %s", story_id, exc)

    return {"ticker": sym, "repaired": repaired, "errors": errors}


def _refs_for_distill(rec: dict[str, Any], em: dict[str, Any]) -> list[dict[str, Any]]:
    stored = list(em.get("references") or [])
    if stored:
        rows = []
        for ref in stored:
            rows.append(
                {
                    "title": ref.get("raw_title") or ref.get("title") or "",
                    "summary": ref.get("raw_summary") or ref.get("summary") or "",
                    "url": ref.get("url") or "",
                    "source": ref.get("publisher") or ref.get("vendor") or "hub",
                    "published_at": ref.get("published_at") or rec.get("published_at") or "",
                    "sources": [
                        {
                            "publisher": ref.get("publisher"),
                            "vendor": ref.get("vendor"),
                            "url": ref.get("url"),
                        }
                    ],
                }
            )
        return rows
    return [_record_to_ref(rec)]


def backfill_distilled_event_metadata(*, ticker: str = "NIFTY") -> dict[str, Any]:
    """Backfill event_id, consensus, and refresh MiniMax-distilled events."""
    import uuid

    from trade_integrations.dataflows.index_research.news_distillation import _consensus_from_refs
    from trade_integrations.dataflows.index_research.news_impact_engine import ingest_headline_rows
    from trade_integrations.hub_storage.verified_news_store import (
        count_verified_records,
        patch_verified_event_meta,
    )

    require_minimax_for_distillation()
    sym = ticker.strip().upper()
    records = list_verified_records(ticker=sym, limit=5000, include_rejected=True)
    row_guard = count_verified_records(ticker=sym)
    meta_patches: list[tuple[str, dict[str, Any]]] = []
    redistill_targets: list[dict[str, Any]] = []
    redistilled = 0
    errors = 0

    for rec in records:
        story_id = str(rec.get("canonical_story_id") or "")
        if not story_id:
            continue
        ss = rec.get("structured_summary") or {}
        em = dict((ss.get("event_meta") if isinstance(ss, dict) else {}) or {})
        missing_meta = not em.get("event_id") or not em.get("consensus")
        needs_redistill = (
            missing_meta
            and (
                is_distillation_leak(str(rec.get("content_summary") or ""))
                or (
                    (em.get("distilled_by") == "minimax" or int(em.get("ref_count") or 0) > 1)
                    and not em.get("event_id")
                )
            )
        )
        if not needs_redistill and not missing_meta:
            continue
        if needs_redistill:
            redistill_targets.append(rec)
        elif missing_meta:
            em["event_id"] = str(em.get("event_id") or uuid.uuid4())
            refs = em.get("references") or []
            if not refs:
                refs = [
                    {
                        "publisher": rec.get("source") or "hub",
                        "vendor": rec.get("source") or "hub",
                        "raw_title": rec.get("title") or "",
                        "raw_summary": rec.get("content_summary") or "",
                        "url": rec.get("url") or "",
                    }
                ]
            em["consensus"] = _consensus_from_refs(refs, tags=rec.get("tags") or {})
            em.setdefault("distilled", False)
            meta_patches.append((story_id, em))

    metadata_only = 0
    if meta_patches:
        metadata_only = patch_verified_event_meta(meta_patches, min_rows=row_guard)

    for rec in redistill_targets:
        story_id = str(rec.get("canonical_story_id") or "")
        ss = rec.get("structured_summary") or {}
        em = dict((ss.get("event_meta") if isinstance(ss, dict) else {}) or {})
        try:
            refs = _refs_for_distill(rec, em)
            prior = dict(rec)
            if is_distillation_leak(str(prior.get("content_summary") or "")):
                prior["content_summary"] = ""
            distilled = distill_event(refs=refs, previous=prior)
            row = _ref_to_process_row(_record_to_ref(rec), event_id=story_id)
            row["sources"] = rec.get("sources") or []
            row = _apply_distilled_to_row(row, distilled)
            if is_distillation_leak(str(row.get("summary") or "")):
                raise RuntimeError("distillation still leaked thinking text")
            publish_day = publish_day_from_value(str(rec.get("published_at") or ""))
            before = count_verified_records(ticker=sym)
            ingest_headline_rows(
                [row],
                ticker=sym,
                collection_day=publish_day or None,
                force_reverify=True,
            )
            after = count_verified_records(ticker=sym)
            if after < before:
                raise RuntimeError(f"row count dropped {before} -> {after} during redistill")
            _log_merge(
                ticker=sym,
                canonical_story_id=story_id,
                row=row,
                merged_story_ids=[],
                reason="backfill_redistill",
            )
            redistilled += 1
        except Exception as exc:
            errors += 1
            logger.warning("backfill failed for %s: %s", story_id, exc)

    return {
        "ticker": sym,
        "redistilled": redistilled,
        "metadata_only": metadata_only,
        "errors": errors,
    }


def compact_duplicate_events(
    *,
    ticker: str = "NIFTY",
    lookback_days: int = 90,
    dry_run: bool = False,
    max_passes: int = 3,
) -> dict[str, Any]:
    """Merge similar hub events within lookback window using MiniMax distillation."""
    totals = {
        "groups_merged": 0,
        "rows_removed": 0,
        "passes": 0,
    }
    last_result: dict[str, Any] = {}
    for _ in range(max(1, max_passes)):
        last_result = _compact_duplicate_events_once(
            ticker=ticker,
            lookback_days=lookback_days,
            dry_run=dry_run,
        )
        totals["passes"] += 1
        totals["groups_merged"] += int(last_result.get("groups_merged") or 0)
        totals["rows_removed"] += int(last_result.get("rows_removed") or 0)
        if dry_run or int(last_result.get("groups_merged") or 0) == 0:
            break
    last_result["groups_merged"] = totals["groups_merged"]
    last_result["rows_removed"] = totals["rows_removed"]
    last_result["passes"] = totals["passes"]
    return last_result


def _compact_duplicate_events_once(
    *,
    ticker: str = "NIFTY",
    lookback_days: int = 90,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Single compaction pass over the lookback window."""
    from datetime import date, timedelta

    from trade_integrations.dataflows.index_research.news_event_matching import find_matching_event
    from trade_integrations.dataflows.index_research.news_impact_engine import ingest_headline_rows
    from trade_integrations.hub_storage.verified_news_store import (
        count_verified_records,
        list_verified_records,
        remove_verified_records,
    )

    require_minimax_for_distillation()
    sym = ticker.strip().upper()
    since = (date.today() - timedelta(days=max(lookback_days, 1))).isoformat()
    records = list_verified_records(
        ticker=sym,
        since=since,
        limit=5000,
        include_rejected=True,
    )
    before_count = len(records)
    consumed: set[str] = set()
    groups_merged = 0
    rows_removed = 0

    for anchor in records:
        anchor_id = str(anchor.get("canonical_story_id") or "")
        if not anchor_id or anchor_id in consumed:
            continue
        group = _build_duplicate_group(anchor, records, ticker=sym, consumed=consumed)

        if len(group) < 2:
            continue

        canonical = max(
            group,
            key=lambda r: (
                not is_distillation_leak(str(r.get("content_summary") or "")),
                len(r.get("sources") or []),
                len(str(r.get("content_summary") or "")),
                str(r.get("first_seen_at") or ""),
            ),
        )
        canon_id = str(canonical.get("canonical_story_id") or "")
        refs = [_record_to_ref(r) for r in group]
        merged_sources: list[dict[str, Any]] = []
        for r in group:
            merged_sources = _merge_sources(merged_sources, r.get("sources") or [])

        if dry_run:
            groups_merged += 1
            rows_removed += len(group) - 1
            consumed.update(str(r.get("canonical_story_id") or "") for r in group)
            continue

        distilled = distill_event(refs=refs, previous=canonical)
        row = _ref_to_process_row(_record_to_ref(canonical), event_id=canon_id)
        row["sources"] = merged_sources
        row = _apply_distilled_to_row(row, distilled)
        publish_day = publish_day_from_value(str(canonical.get("published_at") or ""))
        ingest_headline_rows(
            [row],
            ticker=sym,
            collection_day=publish_day or None,
            force_reverify=True,
        )
        drop_ids = {
            str(r.get("canonical_story_id") or "")
            for r in group
            if str(r.get("canonical_story_id") or "") != canon_id
        }
        rows_removed += remove_verified_records(drop_ids)
        consumed.update(str(r.get("canonical_story_id") or "") for r in group)
        groups_merged += 1
        _log_merge(
            ticker=sym,
            canonical_story_id=canon_id,
            row=row,
            merged_story_ids=sorted(drop_ids),
            reason="compaction",
        )

    after_count = count_verified_records(ticker=sym)
    return {
        "ticker": sym,
        "lookback_days": lookback_days,
        "dry_run": dry_run,
        "before_count_window": before_count,
        "after_count": after_count,
        "groups_merged": groups_merged,
        "rows_removed": rows_removed,
    }


def union_headlines_with_staging(
    records: list[dict[str, Any]],
    *,
    ticker: str = "NIFTY",
    limit: int = 50,
) -> list[dict[str, Any]]:
    """Append unmerged staging refs for live reads."""
    if not is_entity_pipeline_enabled():
        return records[:limit]

    from trade_integrations.hub_storage.news_staging_store import staging_ref_to_headline

    seen_urls = set()
    for rec in records:
        for src in rec.get("sources") or []:
            if isinstance(src, dict) and src.get("url"):
                seen_urls.add(str(src["url"]).strip().lower())

    out = list(records)
    for ref in list_pending_refs(ticker=ticker, limit=limit):
        url = str(ref.get("url") or "").strip().lower()
        if url and url in seen_urls:
            continue
        out.append(staging_ref_to_headline(ref))
        if len(out) >= limit:
            break
    return out[:limit]
