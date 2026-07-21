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
from trade_integrations.dataflows.index_research.news_parent_events import (
    event_parent_id,
    infer_event_kind,
    infer_parent_event_id,
    infer_provenance,
    infer_scope,
)
from trade_integrations.dataflows.index_research.news_tags import build_article_tags
from trade_integrations.hub_storage.news_staging_store import (
    is_entity_pipeline_enabled,
    list_pending_refs,
    mark_ref_merged,
    require_minimax_for_distillation,
)
from trade_integrations.hub_storage.news_merge_ledger import append_merge_event
from trade_integrations.hub_storage.news_events_store import (
    append_distillation_log,
    build_event_from_distilled_row,
    count_events,
    distilled_event_to_headline_dict,
    get_event,
    list_events,
    remove_events,
    upsert_event,
)
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


def _patch_worker_last(patch: dict[str, Any]) -> None:
    """Merge into worker_last.json without dropping prior staging fields."""
    prior = load_worker_last_summary() or {}
    merged = {**prior, **patch}
    _write_worker_last(merged)


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
    em = (row.get("structured_summary") or {}).get("event_meta") or {}
    stable_id = str(em.get("event_id") or row.get("canonical_story_id") or "").strip()
    if stable_id:
        row["canonical_story_id"] = stable_id
    return row


def _pick_primary_ref(refs: list[dict[str, Any]]) -> dict[str, Any]:
    """Prefer the ref with the richest summary for matching/distillation."""
    if not refs:
        return {}
    return max(refs, key=lambda r: len(str(r.get("summary") or r.get("title") or "")))


def _list_match_candidates(
    *,
    ticker: str,
    publish_day: str | None,
    parent_event_id: str | None = None,
) -> list[dict[str, Any]]:
    """Load distilled events for staging match."""
    sym = ticker.strip().upper()
    if parent_event_id:
        events = list_events(
            ticker=sym,
            since=None,
            publish_day=None,
            limit=120,
            include_rejected=False,
        )
        return [
            distilled_event_to_headline_dict(event)
            for event in events
            if event_parent_id(event) == parent_event_id
            or not event_parent_id(event)
        ]
    return [
        distilled_event_to_headline_dict(event)
        for event in list_events(
            ticker=sym,
            publish_day=publish_day or None,
            since=publish_day or None,
            limit=80,
            include_rejected=False,
        )
    ]


def _upsert_distilled_event_store(
    *,
    event_id: str,
    ticker: str,
    row: dict[str, Any],
    distilled: dict[str, Any],
    publish_day: str,
) -> None:
    verified = get_verified_record(event_id) or get_event(event_id)
    verification_status = str((verified or {}).get("verification_status") or "pending")
    event = build_event_from_distilled_row(
        event_id=event_id,
        ticker=ticker,
        row=row,
        distilled=distilled,
        publish_day=publish_day,
        verification_status=verification_status,
    )
    if verified:
        event.predicted_impact = dict(verified.get("predicted_impact") or {})
        event.actual_impact = dict(
            verified.get("actual_impact") or verified.get("actual") or {}
        )
        if verified.get("sources"):
            event.sources = list(verified.get("sources") or [])
    upsert_event(event)


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
    market_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Match ref to hub event, distill, verify, and upsert."""
    return process_staging_group(
        [ref],
        ticker=ticker,
        market_context=market_context,
    )


def process_staging_group(
    refs: list[dict[str, Any]],
    *,
    ticker: str | None = None,
    market_context: dict[str, Any] | None = None,
    adjudication_summary: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Match a deduped ref group to hub event, distill once, verify, and upsert."""
    if not refs:
        return {"action": "skip_empty", "event_id": ""}
    from trade_integrations.dataflows.index_research.news_claim_extraction import enrich_ref_with_claims
    from trade_integrations.dataflows.index_research.news_impact_engine import ingest_headline_rows

    enriched: list[dict[str, Any]] = []
    for ref in refs:
        enriched.append(enrich_ref_with_claims(dict(ref)))
    sym = (ticker or enriched[-1].get("ticker") or "NIFTY").strip().upper()

    from trade_integrations.dataflows.index_research.news_relevance import (
        assess_ref_relevance,
        relevance_min_confidence,
    )
    from trade_integrations.hub_storage.news_staging_store import mark_ref_discarded

    kept: list[dict[str, Any]] = []
    for candidate in enriched:
        verdict = assess_ref_relevance(candidate, ticker=sym)
        if not verdict.relevant and verdict.confidence >= relevance_min_confidence():
            mark_ref_discarded(
                str(candidate.get("ref_id") or ""),
                reason=verdict.reason or "irrelevant",
                relevance=verdict.to_dict(),
                restore_payload=dict(candidate),
                source_kind="auto_gate",
            )
            continue
        kept.append(candidate)
    if not kept:
        return {
            "action": "discard_irrelevant",
            "reason": "all_refs_irrelevant",
            "confidence": relevance_min_confidence(),
        }
    enriched = kept

    from trade_integrations.dataflows.article_body import enrich_ref_summary_from_url

    enriched = [enrich_ref_summary_from_url(r) for r in enriched]
    ref = _pick_primary_ref(enriched)

    if adjudication_summary is None:
        from trade_integrations.dataflows.index_research.news_llm_story_pipeline import (
            adjudication_summary_from_refs,
        )

        adjudication_summary = adjudication_summary_from_refs(enriched)

    publish_day = publish_day_from_value(str(ref.get("published_at") or ""))

    ref_tags = ref.get("tags") if isinstance(ref.get("tags"), dict) else {}
    parent_id = infer_parent_event_id(ref, tags=ref_tags)
    if parent_id:
        ref["parent_event_id"] = parent_id

    matched: dict[str, Any] | None = None
    wiki_hit: dict[str, Any] | None = None
    try:
        from trade_integrations.dataflows.hub_wiki.search_dedup import find_wiki_match_for_record
        from trade_integrations.hub_storage.news_pipeline_config import load_news_pipeline_config

        pipe_cfg = load_news_pipeline_config()
        if pipe_cfg.wiki_search_enabled:
            wiki_hit = find_wiki_match_for_record(
                ref,
                ticker=sym,
                top_k=pipe_cfg.wiki_search_top_k,
                min_score=pipe_cfg.wiki_search_min_score,
            )
            if wiki_hit:
                stored = get_event(str(wiki_hit.get("event_id") or ""))
                if stored:
                    candidate = distilled_event_to_headline_dict(stored)
                    if find_matching_event(ref, [candidate], ticker=sym):
                        matched = candidate
                    else:
                        wiki_hit = None
    except Exception as exc:
        logger.debug("wiki staging match skipped: %s", exc)

    if wiki_hit and matched:
        enrichment = wiki_hit.get("enrichment") or {}
        seen_urls = {str(r.get("url") or "") for r in enriched if r.get("url")}
        for wiki_ref in enrichment.get("references") or []:
            if not isinstance(wiki_ref, dict):
                continue
            url = str(wiki_ref.get("url") or "")
            if not url or url in seen_urls:
                continue
            seen_urls.add(url)
            enriched.append(
                {
                    "title": wiki_ref.get("raw_title") or wiki_ref.get("title") or "",
                    "summary": wiki_ref.get("raw_summary") or wiki_ref.get("summary") or "",
                    "url": url,
                    "source": wiki_ref.get("publisher") or wiki_ref.get("vendor") or "wiki",
                    "published_at": ref.get("published_at") or "",
                }
            )

    if not matched:
        candidates = _list_match_candidates(
            ticker=sym,
            publish_day=publish_day or None,
            parent_event_id=parent_id,
        )
        matched = find_matching_event(ref, candidates, ticker=sym)
    event_id = str(
        matched.get("canonical_story_id") or matched.get("event_id") or ""
    ) if matched else ""

    if not matched:
        url_id = canonical_story_id(str(ref.get("title") or ""), str(ref.get("url") or ""))
        if url_id and get_event(url_id):
            for r in enriched:
                if r.get("ref_id"):
                    mark_ref_merged(str(r["ref_id"]), url_id)
            return {"action": "skip_duplicate_url", "event_id": url_id}

    if matched:
        prior_meta = ((matched.get("structured_summary") or {}).get("event_meta") or {})
        prior_refs = list(prior_meta.get("references") or [])
        merged_sources = matched.get("sources") or []
        for r in enriched:
            merged_sources = _merge_sources(merged_sources, r.get("sources") or [])
        row = _ref_to_process_row(ref, event_id=event_id)
        row["sources"] = merged_sources
        refs_for_distill = [_ref_to_process_row(r) for r in enriched]
        if prior_refs:
            refs_for_distill = prior_refs + refs_for_distill
        distilled = distill_event(
            refs=refs_for_distill,
            previous=matched,
            market_context=market_context,
            adjudication_summary=adjudication_summary,
            canonical_event_id=event_id,
        )
        row = _apply_distilled_to_row(row, distilled)
        action = "update"
    else:
        event_id = canonical_story_id(str(ref.get("title") or ""), str(ref.get("url") or ""))
        row = _ref_to_process_row(ref, event_id=event_id)
        distilled = distill_event(
            refs=enriched,
            previous=None,
            market_context=market_context,
            adjudication_summary=adjudication_summary,
            canonical_event_id=event_id,
        )
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
    for r in enriched:
        if r.get("ref_id"):
            mark_ref_merged(str(r["ref_id"]), event_id)
    wiki_result: dict[str, Any] | None = None
    try:
        from trade_integrations.dataflows.hub_wiki.compile import compile_event_to_wiki, wiki_compile_enabled

        if wiki_compile_enabled():
            meta = (row.get("structured_summary") or {}).get("event_meta") or {}
            lookup_id = str(meta.get("event_id") or event_id)
            stored = get_event(lookup_id) or get_event(event_id)
            payload = stored or {
                "event_id": lookup_id,
                "ticker": sym,
                "title": row.get("title"),
                "content": row.get("summary") or distilled.get("content"),
                "publish_day": publish_day,
                "structured_summary": row.get("structured_summary"),
                "verification_status": "pending",
            }
            wiki_result = compile_event_to_wiki(payload, rescan=False)
    except Exception as exc:
        logger.debug("llm-wiki compile skipped for %s: %s", event_id, exc)

    out = {
        "action": action,
        "event_id": event_id,
        "ref_count": len(enriched),
        "stats": stats,
    }
    if wiki_result:
        out["wiki_compile"] = wiki_result
    return out


def _adaptive_drain_batch_size(*, ticker: str | None = None) -> int:
    from trade_integrations.hub_storage.news_staging_store import staging_queue_detail

    queued = int(staging_queue_detail(ticker=ticker).get("queued") or 0)
    if queued <= 0:
        return 50
    return min(500, max(50, queued // 4))


def _background_drain_limit(*, ticker: str | None = None) -> int:
    from trade_integrations.hub_storage.news_staging_store import staging_queue_detail

    queued = int(staging_queue_detail(ticker=ticker).get("queued") or 0)
    return min(100, max(20, queued // 20 if queued > 0 else 20))


def process_staging_batch(
    *,
    ticker: str | None = None,
    limit: int = 20,
    force_reverify: bool = False,
    run_wiki_rescan: bool = False,
) -> dict[str, Any]:
    """Process up to ``limit`` queued staging refs."""
    if not is_entity_pipeline_enabled():
        return {"processed": 0, "skipped": True}

    from trade_integrations.hub_storage.news_staging_store import pipeline_pause_status

    pause = pipeline_pause_status(ticker=ticker)
    if pause.get("pipeline_paused"):
        return {
            "processed": 0,
            "created": 0,
            "updated": 0,
            "skipped": 0,
            "errors": 0,
            "paused": True,
            "pipeline_paused": True,
            "pause_reason": pause.get("pause_reason") or "",
            "pending": pause.get("pending") or {},
        }

    require_minimax_for_distillation()

    from trade_integrations.dataflows.index_research.news_market_context import (
        get_market_context_for_pipeline,
    )

    sym = (ticker or "NIFTY").strip().upper()
    market_context = get_market_context_for_pipeline(ticker=sym, refresh=False)

    pending = list_pending_refs(ticker=ticker, limit=limit)
    cluster_stats: dict[str, int] = {}
    try:
        from trade_integrations.dataflows.index_research.news_embedding_cluster import (
            dedupe_pending_by_cluster,
        )

        pending, cluster_stats = dedupe_pending_by_cluster(pending, ticker=ticker)
    except Exception as exc:
        logger.debug("tier-2 cluster dedupe skipped: %s", exc)

    from trade_integrations.dataflows.index_research.news_llm_story_pipeline import (
        run_story_pipeline_batch,
    )

    try:
        groups, pipeline_stats = run_story_pipeline_batch(pending, market_context=market_context)
    except Exception as exc:
        logger.warning("Story pipeline failed, using singleton groups: %s", exc)
        from trade_integrations.dataflows.index_research.news_llm_story_pipeline import (
            mechanical_singleton_groups,
        )

        groups = mechanical_singleton_groups(pending)
        pipeline_stats = {
            "adjudication_discarded": 0,
            "adjudication_exaggerated": 0,
            "adjudication_valid": 0,
            "adjudication_rule_discarded": 0,
            "adjudication_fallback": 0,
            "story_groups_fallback": True,
            "llm_dedup_groups": len(groups),
            "mechanical_refs": len(pending),
        }

    summary: dict[str, Any] = {
        "processed": 0,
        "created": 0,
        "updated": 0,
        "skipped": 0,
        "errors": 0,
        "cluster_dedup": cluster_stats,
        "llm_dedup_groups": pipeline_stats.get("llm_dedup_groups", len(groups)),
        "mechanical_refs": pipeline_stats.get("mechanical_refs", len(pending)),
        "market_context_as_of": market_context.get("as_of"),
        "wiki_exports": 0,
        "adjudication_discarded": pipeline_stats.get("adjudication_discarded", 0),
        "adjudication_exaggerated": pipeline_stats.get("adjudication_exaggerated", 0),
        "adjudication_valid": pipeline_stats.get("adjudication_valid", 0),
        "adjudication_rule_discarded": pipeline_stats.get("adjudication_rule_discarded", 0),
        "adjudication_fallback": pipeline_stats.get("adjudication_fallback", 0),
        "story_groups_fallback": pipeline_stats.get("story_groups_fallback", False),
    }
    for group in groups:
        group_refs = group.get("refs")
        if not isinstance(group_refs, list) or not group_refs:
            id_map = {str(r.get("ref_id") or ""): r for r in pending if r.get("ref_id")}
            group_refs = [id_map[rid] for rid in group.get("ref_ids") or [] if rid in id_map]
        if not group_refs:
            continue
        try:
            result = process_staging_group(
                group_refs,
                ticker=ticker,
                market_context=market_context,
                adjudication_summary=group.get("adjudication_summary")
                if isinstance(group.get("adjudication_summary"), dict)
                else None,
            )
            summary["processed"] += int(result.get("ref_count") or len(group_refs))
            action = result.get("action")
            if action == "create":
                summary["created"] += 1
            elif action == "update":
                summary["updated"] += 1
            elif action in {"skip_duplicate_url", "discard_irrelevant", "skip_empty"}:
                summary["skipped"] += 1
            wiki_compile = result.get("wiki_compile")
            if isinstance(wiki_compile, dict) and wiki_compile.get("ok"):
                summary["wiki_exports"] = int(summary.get("wiki_exports") or 0) + 1
        except Exception as exc:
            summary["errors"] += 1
            logger.warning(
                "staging group %s failed: %s",
                group.get("group_id"),
                exc,
            )
    if run_wiki_rescan and int(summary.get("wiki_exports") or 0) > 0:
        try:
            from trade_integrations.dataflows.hub_wiki.compile import batch_rescan_if_enabled

            summary["wiki_rescan"] = batch_rescan_if_enabled()
        except Exception as exc:
            logger.debug("llm-wiki batch rescan skipped: %s", exc)
    if summary.get("processed"):
        try:
            _write_worker_last({"ticker": ticker, **summary})
        except Exception as exc:
            logger.debug("worker last summary write failed: %s", exc)
    return summary


def schedule_staging_processing(*, ticker: str | None = None, limit: int | None = None) -> None:
    """Fire-and-forget background batch (debounced)."""
    import time

    global _last_run_at
    if not is_entity_pipeline_enabled():
        return

    from trade_integrations.hub_storage.news_staging_store import pipeline_pause_status, staging_queue_detail

    if pipeline_pause_status(ticker=ticker).get("pipeline_paused"):
        return

    queued = int(staging_queue_detail(ticker=ticker).get("queued") or 0)
    batch_limit = limit if limit is not None else _background_drain_limit(ticker=ticker)
    debounce_sec = 10.0 if queued > 500 else 30.0

    now = time.time()
    if now - _last_run_at < debounce_sec:
        return

    def _run() -> None:
        global _last_run_at
        with _worker_lock:
            _last_run_at = time.time()
            try:
                process_staging_batch(ticker=ticker, limit=batch_limit)
            except Exception as exc:
                logger.debug("background staging batch failed: %s", exc)

    threading.Thread(target=_run, daemon=True, name="hub-news-entity-worker").start()


def _safe_stage(name: str, fn: Any, /, **kwargs: Any) -> dict[str, Any]:
    """Run a pipeline stage; return error dict instead of raising."""
    try:
        result = fn(**kwargs)
        if isinstance(result, dict):
            return result
        return {"status": "ok", "result": result}
    except Exception as exc:
        logger.exception("hub news entity stage %s failed", name)
        return {"status": "error", "stage": name, "error": str(exc)}


def _refresh_news_impact_cache(*, ticker: str) -> dict[str, Any]:
    try:
        from trade_integrations.dataflows.news_hub_bridge import refresh_news_impact

        return refresh_news_impact(ticker=ticker, refresh_ingest=False)
    except Exception as exc:
        logger.warning("news_impact cache refresh failed: %s", exc)
        return {"status": "error", "error": str(exc)[:200]}


def run_hub_news_entity_job(config: dict[str, Any] | None = None) -> dict[str, Any]:
    """Daily compaction: drain staging queue and process pending refs."""
    from trade_integrations.hub_storage.news_migrations import ensure_hub_news_migrations
    from trade_integrations.hub_storage.news_staging_store import pipeline_pause_status

    cfg = config or {}
    ticker = str(cfg.get("ticker") or "NIFTY")
    batch_raw = cfg.get("batch_size")
    if cfg.get("adaptive_batch") or batch_raw == "adaptive":
        limit = _adaptive_drain_batch_size(ticker=ticker)
    else:
        limit = int(batch_raw or 200)
    lookback = int(cfg.get("lookback_days") or 365)
    mode = str(cfg.get("mode") or "full").strip().lower()
    run_maintenance = mode in {"full", "maintenance"}
    run_drain = mode in {"full", "drain"}
    run_wiki_rescan = bool(cfg.get("run_wiki_rescan"))

    migration = _safe_stage("migration", ensure_hub_news_migrations, ticker=ticker)
    pause = pipeline_pause_status(ticker=ticker)

    staging: dict[str, Any] = {"skipped": True, "reason": "drain_disabled"}
    if run_drain:
        staging_parts: list[dict[str, Any]] = []
        primary_sym = ticker.strip().upper()
        staging_parts.append(
            _safe_stage(
                "staging",
                process_staging_batch,
                ticker=primary_sym,
                limit=limit,
                force_reverify=False,
                run_wiki_rescan=run_wiki_rescan,
            )
        )
        for sym in _tickers_with_pending_staging():
            if sym == primary_sym:
                continue
            if pause.get("pipeline_paused"):
                break
            staging_parts.append(
                _safe_stage(
                    f"staging_{sym}",
                    process_staging_batch,
                    ticker=sym,
                    limit=min(limit, 50),
                    force_reverify=False,
                    run_wiki_rescan=False,
                )
            )
        staging = _merge_staging_summaries(staging_parts)

    if pause.get("pipeline_paused"):
        skipped = {
            "skipped": True,
            "pipeline_paused": True,
            "pause_reason": str(pause.get("pause_reason") or ""),
        }
        return {
            "mode": mode,
            "migration": migration,
            "staging": staging,
            "repair": dict(skipped),
            "backfill": dict(skipped),
            "compact_events": dict(skipped),
            "pipeline_paused": True,
            "pause_reason": pause.get("pause_reason") or "",
            "had_errors": any(
                isinstance(part, dict) and part.get("status") == "error"
                for part in (migration, staging)
            ),
        }

    if not run_maintenance:
        compact_events = _safe_stage(
            "compact_events",
            compact_distilled_events,
            ticker=ticker,
            lookback_days=7,
            max_passes=1,
        )
        news_impact = _refresh_news_impact_cache(ticker=ticker) if run_drain else {"skipped": True}
        drain_result = {
            "mode": mode,
            "migration": migration,
            "staging": staging,
            "repair": {"skipped": True, "reason": "drain_only"},
            "backfill": {"skipped": True, "reason": "drain_only"},
            "compact_events": compact_events,
            "cleanup": {"skipped": True, "reason": "drain_only"},
            "rollup": {"skipped": True, "reason": "drain_only"},
            "news_impact_refresh": news_impact,
            "had_errors": any(
                isinstance(part, dict) and part.get("status") == "error"
                for part in (migration, staging, compact_events, news_impact)
            ),
        }
        if isinstance(compact_events, dict):
            wiki_block = {
                "wiki_groups_merged": compact_events.get("wiki_groups_merged"),
                "wiki_search_queries": compact_events.get("wiki_search_queries"),
                "wiki_files_removed": compact_events.get("wiki_files_removed"),
            }
            drain_result["wiki_compaction"] = wiki_block
            try:
                _patch_worker_last({"ticker": ticker, **wiki_block, "compact_events": compact_events})
            except Exception as exc:
                logger.debug("worker last wiki compaction summary write failed: %s", exc)
        return drain_result

    repair = _safe_stage("repair", repair_leaked_distilled_summaries, ticker=ticker)
    backfill = _safe_stage("backfill", backfill_distilled_event_metadata, ticker=ticker)
    compact_events = _safe_stage(
        "compact_events",
        compact_distilled_events,
        ticker=ticker,
        lookback_days=lookback,
    )
    from trade_integrations.dataflows.index_research.news_cleanup import cleanup_hub_news
    from trade_integrations.dataflows.index_research.news_rollup import rollup_parent_topic_events

    cleanup = _safe_stage("cleanup", cleanup_hub_news, ticker=ticker)
    rollup = _safe_stage(
        "rollup",
        rollup_parent_topic_events,
        ticker=ticker,
        lookback_days=7,
    )
    stages = (migration, staging, repair, backfill, compact_events, cleanup, rollup)
    news_impact = _refresh_news_impact_cache(ticker=ticker) if run_drain else {"skipped": True}
    result = {
        "mode": mode,
        "migration": migration,
        "staging": staging,
        "repair": repair,
        "backfill": backfill,
        "compact_events": compact_events,
        "cleanup": cleanup,
        "rollup": rollup,
        "news_impact_refresh": news_impact,
        "had_errors": any(
            isinstance(part, dict) and part.get("status") == "error"
            for part in (*stages, news_impact)
        ),
    }
    if isinstance(compact_events, dict):
        wiki_block = {
            "wiki_groups_merged": compact_events.get("wiki_groups_merged"),
            "wiki_search_queries": compact_events.get("wiki_search_queries"),
            "wiki_files_removed": compact_events.get("wiki_files_removed"),
        }
        result["wiki_compaction"] = wiki_block
        try:
            _patch_worker_last({"ticker": ticker, **wiki_block, "compact_events": compact_events})
        except Exception as exc:
            logger.debug("worker last wiki compaction summary write failed: %s", exc)
    return result


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


def _pick_canonical_from_group(group: list[dict[str, Any]]) -> dict[str, Any]:
    return max(
        group,
        key=lambda r: (
            not is_distillation_leak(str(r.get("content_summary") or r.get("content") or "")),
            len(r.get("sources") or []),
            len(r.get("references") or []),
            len(str(r.get("content_summary") or r.get("content") or "")),
            str(r.get("first_seen_at") or ""),
        ),
    )


def _wiki_ref_to_distill_ref(wiki_ref: dict[str, Any]) -> dict[str, Any]:
    return {
        "title": wiki_ref.get("raw_title") or wiki_ref.get("title") or "",
        "summary": wiki_ref.get("raw_summary") or wiki_ref.get("summary") or "",
        "url": wiki_ref.get("url") or "",
        "source": wiki_ref.get("publisher") or wiki_ref.get("vendor") or "wiki",
        "published_at": wiki_ref.get("published_at") or "",
    }


def _enrich_refs_from_wiki(
    group: list[dict[str, Any]],
    wiki_index: dict[str, Any],
) -> list[dict[str, Any]]:
    from trade_integrations.dataflows.hub_wiki.search_dedup import load_wiki_enrichment

    refs = [_record_to_ref(r) for r in group]
    seen_urls = {str(r.get("url") or "") for r in refs if r.get("url")}
    for record in group:
        event_id = str(record.get("canonical_story_id") or record.get("event_id") or "")
        if not event_id:
            continue
        enrichment = load_wiki_enrichment(event_id, wiki_index)
        for wiki_ref in enrichment.get("references") or []:
            if not isinstance(wiki_ref, dict):
                continue
            converted = _wiki_ref_to_distill_ref(wiki_ref)
            url = str(converted.get("url") or "")
            if url and url not in seen_urls:
                seen_urls.add(url)
                refs.append(converted)
    return refs


def _cleanup_wiki_after_merge(
    *,
    canon_id: str,
    dropped_events: list[dict[str, Any]],
    pre_canonical_snapshot: dict[str, Any] | None,
    dry_run: bool,
) -> int:
    if dry_run:
        return len(dropped_events)
    removed = 0
    try:
        from trade_integrations.dataflows.hub_wiki.compile import (
            compile_event_to_wiki,
            remove_event_wiki_files,
            wiki_compile_enabled,
        )

        if not wiki_compile_enabled():
            return 0
        for dropped in dropped_events:
            if dropped:
                result = remove_event_wiki_files(dropped, rescan=False)
                removed += len(result.get("removed") or [])
        if pre_canonical_snapshot:
            result = remove_event_wiki_files(pre_canonical_snapshot, rescan=False)
            removed += len(result.get("removed") or [])
        stored = get_event(canon_id)
        if stored:
            compile_event_to_wiki(stored, rescan=False)
    except Exception as exc:
        logger.debug("wiki cleanup after merge skipped: %s", exc)
    return removed


def _merge_duplicate_group(
    group: list[dict[str, Any]],
    *,
    ticker: str,
    consumed: set[str],
    dry_run: bool,
    reason: str,
    wiki_index: dict[str, Any] | None = None,
    preferred_canon_id: str | None = None,
) -> dict[str, int]:
    """Merge one duplicate group into canonical SSOT row."""
    if len(group) < 2:
        return {"groups_merged": 0, "rows_removed": 0, "wiki_files_removed": 0}

    sym = ticker.strip().upper()
    group_ids = {
        str(r.get("canonical_story_id") or r.get("event_id") or "")
        for r in group
    }
    if group_ids & consumed:
        return {"groups_merged": 0, "rows_removed": 0, "wiki_files_removed": 0}

    canonical: dict[str, Any]
    if preferred_canon_id:
        preferred = next(
            (
                r
                for r in group
                if str(r.get("canonical_story_id") or r.get("event_id") or "") == preferred_canon_id
            ),
            None,
        )
        canonical = preferred if preferred else _pick_canonical_from_group(group)
    else:
        canonical = _pick_canonical_from_group(group)
    canon_id = str(canonical.get("canonical_story_id") or canonical.get("event_id") or "")
    refs = _enrich_refs_from_wiki(group, wiki_index or {"by_event_id": {}, "by_slug": {}})
    merged_sources: list[dict[str, Any]] = []
    for r in group:
        merged_sources = _merge_sources(merged_sources, r.get("sources") or [])

    drop_ids = {
        str(r.get("canonical_story_id") or r.get("event_id") or "")
        for r in group
        if str(r.get("canonical_story_id") or r.get("event_id") or "") != canon_id
    }

    if dry_run:
        consumed.update(group_ids)
        return {
            "groups_merged": 1,
            "rows_removed": len(group) - 1,
            "wiki_files_removed": len(drop_ids) + (1 if reason == "events_compaction_wiki" else 0),
        }

    distilled = distill_event(refs=refs, previous=canonical, canonical_event_id=canon_id)
    row = _ref_to_process_row(_record_to_ref(canonical), event_id=canon_id)
    row["sources"] = merged_sources
    row = _apply_distilled_to_row(row, distilled)
    publish_day = publish_day_from_value(str(canonical.get("published_at") or ""))

    pre_wiki_snapshot = get_event(canon_id)
    dropped_events = [get_event(drop_id) for drop_id in drop_ids]
    _upsert_distilled_event_store(
        event_id=canon_id,
        ticker=sym,
        row=row,
        distilled=distilled,
        publish_day=publish_day or "",
    )
    rows_removed = remove_events(drop_ids)
    wiki_files_removed = _cleanup_wiki_after_merge(
        canon_id=canon_id,
        dropped_events=[e for e in dropped_events if e],
        pre_canonical_snapshot=pre_wiki_snapshot,
        dry_run=False,
    )
    consumed.update(group_ids)
    _log_merge(
        ticker=sym,
        canonical_story_id=canon_id,
        row=row,
        merged_story_ids=sorted(drop_ids),
        reason=reason,
    )
    try:
        append_distillation_log(
            {
                "ticker": sym,
                "reason": reason,
                "canonical_event_id": canon_id,
                "removed_event_ids": sorted(drop_ids),
                "ref_count": len(refs),
                "publish_day": publish_day,
            }
        )
    except Exception as exc:
        logger.debug("distillation log append failed: %s", exc)

    return {
        "groups_merged": 1,
        "rows_removed": rows_removed,
        "wiki_files_removed": wiki_files_removed,
    }


def _build_duplicate_group(
    anchor: dict[str, Any],
    records: list[dict[str, Any]],
    *,
    ticker: str,
    consumed: set[str],
) -> list[dict[str, Any]]:
    """Collect records similar to anchor (semantic search + rule match)."""
    from trade_integrations.dataflows.index_research.news_entity_semantic_dedup import (
        build_duplicate_groups_semantic,
    )

    anchor_id = str(anchor.get("canonical_story_id") or anchor.get("event_id") or "")
    if not anchor_id or anchor_id in consumed:
        return [anchor]

    groups = build_duplicate_groups_semantic(
        records,
        ticker=ticker,
        consumed=consumed,
    )
    for group in groups:
        ids = {
            str(r.get("canonical_story_id") or r.get("event_id") or "")
            for r in group
        }
        if anchor_id in ids:
            return group

    from trade_integrations.dataflows.index_research.news_event_matching import find_matching_event

    group = [anchor]
    group_ids = {anchor_id}
    for other in records:
        oid = str(other.get("canonical_story_id") or other.get("event_id") or "")
        if not oid or oid in consumed or oid in group_ids:
            continue
        ref = _record_to_ref(other)
        ref["tags"] = other.get("tags") or ref.get("tags")
        if find_matching_event(ref, [anchor], ticker=ticker):
            group.append(other)
            group_ids.add(oid)
    return group


def _tickers_with_pending_staging() -> list[str]:
    from trade_integrations.hub_storage.news_staging_store import list_pending_refs

    tickers: set[str] = set()
    for ref in list_pending_refs(ticker=None, limit=10_000):
        sym = str(ref.get("ticker") or "NIFTY").strip().upper()
        if sym:
            tickers.add(sym)
    return sorted(tickers)


def _merge_staging_summaries(parts: list[dict[str, Any]]) -> dict[str, Any]:
    if not parts:
        return {"processed": 0, "created": 0, "updated": 0, "skipped": 0, "errors": 0}
    if len(parts) == 1:
        return parts[0]
    merged: dict[str, Any] = {
        "processed": 0,
        "created": 0,
        "updated": 0,
        "skipped": 0,
        "errors": 0,
        "tickers": [],
    }
    for part in parts:
        if not isinstance(part, dict):
            continue
        for key in ("processed", "created", "updated", "skipped", "errors"):
            merged[key] = int(merged.get(key) or 0) + int(part.get(key) or 0)
        sym = part.get("ticker")
        if sym:
            merged["tickers"].append(sym)
    return merged


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
            em["event_id"] = str(em.get("event_id") or story_id or uuid.uuid4())
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
            distilled = distill_event(refs=refs, previous=prior, canonical_event_id=story_id)
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


def compact_distilled_events(
    *,
    ticker: str = "NIFTY",
    lookback_days: int = 90,
    dry_run: bool = False,
    max_passes: int = 3,
) -> dict[str, Any]:
    """Merge duplicate distilled events in events.parquet SSOT."""
    if count_events(ticker=ticker) == 0:
        return {
            "ticker": ticker.strip().upper(),
            "lookback_days": lookback_days,
            "dry_run": dry_run,
            "groups_merged": 0,
            "rows_removed": 0,
            "passes": 0,
            "skipped": True,
        }

    totals = {
        "groups_merged": 0,
        "rows_removed": 0,
        "passes": 0,
        "wiki_groups_merged": 0,
        "wiki_search_queries": 0,
        "wiki_files_removed": 0,
    }
    last_result: dict[str, Any] = {}
    for _ in range(max(1, max_passes)):
        last_result = _compact_distilled_events_once(
            ticker=ticker,
            lookback_days=lookback_days,
            dry_run=dry_run,
        )
        totals["passes"] += 1
        totals["groups_merged"] += int(last_result.get("groups_merged") or 0)
        totals["rows_removed"] += int(last_result.get("rows_removed") or 0)
        totals["wiki_groups_merged"] += int(last_result.get("wiki_groups_merged") or 0)
        totals["wiki_search_queries"] += int(last_result.get("wiki_search_queries") or 0)
        totals["wiki_files_removed"] += int(last_result.get("wiki_files_removed") or 0)
        if dry_run or int(last_result.get("groups_merged") or 0) == 0:
            break
    last_result["groups_merged"] = totals["groups_merged"]
    last_result["rows_removed"] = totals["rows_removed"]
    last_result["passes"] = totals["passes"]
    last_result["wiki_groups_merged"] = totals["wiki_groups_merged"]
    last_result["wiki_search_queries"] = totals["wiki_search_queries"]
    last_result["wiki_files_removed"] = totals["wiki_files_removed"]
    return last_result


def _compact_distilled_events_once(
    *,
    ticker: str = "NIFTY",
    lookback_days: int = 90,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Single compaction pass over distilled events parquet."""
    from datetime import date, timedelta

    require_minimax_for_distillation()
    sym = ticker.strip().upper()
    since = (date.today() - timedelta(days=max(lookback_days, 1))).isoformat()
    raw_events = list_events(
        ticker=sym,
        since=since,
        limit=5000,
        include_rejected=True,
    )
    records = [distilled_event_to_headline_dict(event) for event in raw_events]
    before_count = len(records)
    consumed: set[str] = set()
    groups_merged = 0
    rows_removed = 0
    wiki_groups_merged = 0
    wiki_search_queries = 0
    wiki_files_removed = 0

    from trade_integrations.dataflows.hub_wiki.search_dedup import (
        build_duplicate_groups_wiki,
        build_source_event_index,
        reset_wiki_search_availability_cache,
        wiki_search_available,
    )
    from trade_integrations.dataflows.index_research.news_entity_semantic_dedup import (
        build_duplicate_groups_semantic,
    )
    from trade_integrations.hub_storage.news_pipeline_config import load_news_pipeline_config

    pipe_cfg = load_news_pipeline_config()
    wiki_index = build_source_event_index()
    reset_wiki_search_availability_cache()
    wiki_ok = wiki_search_available(enabled=pipe_cfg.wiki_search_enabled)

    if wiki_ok:
        wiki_groups, wiki_stats = build_duplicate_groups_wiki(
            records,
            ticker=sym,
            consumed=consumed,
            max_queries=pipe_cfg.wiki_search_max_per_pass,
            index=wiki_index,
            wiki_available=True,
            top_k=pipe_cfg.wiki_search_top_k,
            min_score=pipe_cfg.wiki_search_min_score,
        )
        wiki_search_queries += int(wiki_stats.get("wiki_search_queries") or 0)
        for group, wiki_target_id in wiki_groups:
            result = _merge_duplicate_group(
                group,
                ticker=sym,
                consumed=consumed,
                dry_run=dry_run,
                reason="events_compaction_wiki",
                wiki_index=wiki_index,
                preferred_canon_id=wiki_target_id,
            )
            if result["groups_merged"]:
                wiki_groups_merged += 1
            groups_merged += int(result.get("groups_merged") or 0)
            rows_removed += int(result.get("rows_removed") or 0)
            wiki_files_removed += int(result.get("wiki_files_removed") or 0)
            if not dry_run and int(result.get("groups_merged") or 0) > 0:
                wiki_index = build_source_event_index()

    if (
        not dry_run
        and wiki_ok
        and (wiki_groups_merged > 0 or wiki_files_removed > 0)
    ):
        try:
            from trade_integrations.dataflows.hub_wiki.compile import batch_rescan_if_enabled

            batch_rescan_if_enabled()
        except Exception as exc:
            logger.debug("llm-wiki post-compaction rescan skipped: %s", exc)

    duplicate_groups = build_duplicate_groups_semantic(records, ticker=sym, consumed=consumed)
    for group in duplicate_groups:
        result = _merge_duplicate_group(
            group,
            ticker=sym,
            consumed=consumed,
            dry_run=dry_run,
            reason="events_compaction",
            wiki_index=wiki_index,
        )
        groups_merged += int(result.get("groups_merged") or 0)
        rows_removed += int(result.get("rows_removed") or 0)
        wiki_files_removed += int(result.get("wiki_files_removed") or 0)

    for anchor in records:
        anchor_id = str(anchor.get("canonical_story_id") or anchor.get("event_id") or "")
        if not anchor_id or anchor_id in consumed:
            continue
        group = _build_duplicate_group(anchor, records, ticker=sym, consumed=consumed)
        result = _merge_duplicate_group(
            group,
            ticker=sym,
            consumed=consumed,
            dry_run=dry_run,
            reason="events_compaction",
            wiki_index=wiki_index,
        )
        groups_merged += int(result.get("groups_merged") or 0)
        rows_removed += int(result.get("rows_removed") or 0)
        wiki_files_removed += int(result.get("wiki_files_removed") or 0)

    after_count = count_events(ticker=sym)
    return {
        "ticker": sym,
        "lookback_days": lookback_days,
        "dry_run": dry_run,
        "before_count_window": before_count,
        "after_count": after_count,
        "groups_merged": groups_merged,
        "rows_removed": rows_removed,
        "wiki_groups_merged": wiki_groups_merged,
        "wiki_search_queries": wiki_search_queries,
        "wiki_files_removed": wiki_files_removed,
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

    from trade_integrations.hub_storage.news_staging_store import (
        collect_distilled_urls,
        filter_staging_refs_not_in_urls,
        list_pending_refs,
        staging_ref_to_headline,
    )

    seen_urls = collect_distilled_urls(records)
    out = list(records)
    pending = filter_staging_refs_not_in_urls(
        list_pending_refs(ticker=ticker, limit=limit),
        seen_urls,
    )
    for ref in pending:
        out.append(staging_ref_to_headline(ref))
        if len(out) >= limit:
            break
    return out[:limit]
