"""Step 10 — capped maintainer backfill for thin legacy refs (pipeline steps 02–04)."""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone
from typing import Any

logger = logging.getLogger(__name__)

STEP_ID = "step_10_backfill_maintainer"


def _enrichment_blob(ref: dict[str, Any]) -> dict[str, Any]:
    enrichment = ref.get("article_enrichment")
    if isinstance(enrichment, dict) and enrichment:
        return enrichment
    structured = ref.get("structured_enrichment")
    if isinstance(structured, dict) and structured:
        return structured
    return {}


def ref_has_enrichment_signal(ref: dict[str, Any]) -> bool:
    blob = _enrichment_blob(ref)
    return bool(blob.get("cause_indicators") or blob.get("future_events"))


def ref_needs_enrichment_backfill(ref: dict[str, Any]) -> bool:
    if not isinstance(ref, dict):
        return False
    if ref.get("enrichment_backfill_at"):
        return False
    if ref_has_enrichment_signal(ref):
        return False
    enrichment = ref.get("article_enrichment")
    if isinstance(enrichment, dict) and enrichment.get("relevant") is False:
        return False
    url = str(ref.get("url") or "").strip()
    title = str(ref.get("title") or ref.get("raw_title") or "").strip()
    if not url and not title:
        return False
    return True


def ref_needs_force_reenrich(ref: dict[str, Any]) -> bool:
    if ref.get("enrichment_backfill_at"):
        return False
    enrichment = ref.get("article_enrichment")
    return isinstance(enrichment, dict) and bool(enrichment) and not ref_has_enrichment_signal(ref)


def pipeline_payload_from_event_ref(
    ref: dict[str, Any],
    *,
    event: dict[str, Any],
) -> dict[str, Any]:
    payload = dict(ref)
    payload.setdefault(
        "ref_id",
        str(ref.get("ref_id") or ref.get("url") or event.get("event_id") or "")[:120],
    )
    payload["title"] = str(ref.get("title") or ref.get("raw_title") or "")[:300]
    payload["summary"] = str(ref.get("summary") or ref.get("raw_summary") or "")[:2000]
    payload["url"] = str(ref.get("url") or "")
    payload["published_at"] = str(
        ref.get("published_at") or event.get("published_at") or event.get("publish_day") or ""
    )
    payload["_relevance_prefiltered"] = True
    if ref_needs_force_reenrich(ref):
        payload["_force_re_enrich"] = True
    return payload


def sync_structured_enrichment(ref: dict[str, Any]) -> dict[str, Any]:
    """Mirror step 07 fields so downstream consumers see causes on the ref."""
    from trade_integrations.dataflows.index_research.hub_news_pipeline.step_07_event_distill_bridge import (
        format_enrichment_distill_block,
    )

    out = dict(ref)
    enrichment = out.get("article_enrichment")
    if not isinstance(enrichment, dict) or not enrichment:
        return out

    block = format_enrichment_distill_block(enrichment)
    if block:
        out["pipeline_distill_hints"] = block
    else:
        out.pop("pipeline_distill_hints", None)
    out["structured_enrichment"] = {
        "cause_indicators": list(enrichment.get("cause_indicators") or []),
        "future_events": list(enrichment.get("future_events") or []),
        "article_opinions": list(enrichment.get("article_opinions") or []),
    }
    return out


def merge_pipeline_ref(existing: dict[str, Any], enriched: dict[str, Any]) -> dict[str, Any]:
    merged = {**existing, **enriched}
    for key in (
        "article_enrichment",
        "structured_enrichment",
        "pipeline_distill_hints",
        "pipeline_step_trace",
        "published_at",
        "summary",
        "raw_summary",
    ):
        if key in enriched and enriched.get(key) not in (None, "", {}):
            merged[key] = enriched[key]
    if enriched.get("summary") and not merged.get("raw_summary"):
        merged["raw_summary"] = enriched["summary"]
    return sync_structured_enrichment(merged)


def _meaningful_ref_changed(before: dict[str, Any], after: dict[str, Any]) -> bool:
    keys = (
        "article_enrichment",
        "structured_enrichment",
        "pipeline_distill_hints",
        "published_at",
        "summary",
        "raw_summary",
        "enrichment_backfill_at",
    )
    return any(before.get(key) != after.get(key) for key in keys)


def run_enrichment_backfill(
    *,
    ticker: str = "NIFTY",
    lookback_days: int = 90,
    limit: int = 50,
) -> dict[str, Any]:
    """Re-run pipeline steps 02–04 on thin legacy event refs."""
    from trade_integrations.dataflows.company_research.market import india_trading_date_iso
    from trade_integrations.dataflows.index_research.hub_news_pipeline.pipeline_runner import (
        MAINTAINER_THROUGH,
        hub_news_pipeline_enabled,
        run_ref_pipeline,
    )
    from trade_integrations.hub_storage.news_events_store import (
        get_event,
        list_events,
        patch_event_meta,
    )
    from trade_integrations.hub_storage.news_staging_store import pipeline_pause_status

    sym = ticker.strip().upper()
    if not hub_news_pipeline_enabled():
        return {
            "ticker": sym,
            "skipped": True,
            "reason": "HUB_NEWS_PIPELINE_ENABLED=0",
        }

    pause = pipeline_pause_status(ticker=sym)
    if pause.get("pipeline_paused"):
        return {
            "ticker": sym,
            "skipped": True,
            "pipeline_paused": True,
            "pause_reason": str(pause.get("pause_reason") or ""),
        }

    capped_lookback = min(max(lookback_days, 1), 90)
    capped_limit = min(max(limit, 1), 50)
    today = india_trading_date_iso()[:10]
    since = (date.fromisoformat(today) - timedelta(days=capped_lookback)).isoformat()
    raw_events = list_events(ticker=sym, since=since, limit=5000, include_rejected=False)

    candidates: list[tuple[str, list[dict[str, Any]]]] = []
    refs_targeted = 0
    for event in raw_events:
        eid = str(event.get("event_id") or "").strip()
        if not eid:
            continue
        structured = event.get("structured_summary") if isinstance(event.get("structured_summary"), dict) else {}
        em = structured.get("event_meta") if isinstance(structured.get("event_meta"), dict) else {}
        refs = [dict(r) for r in (em.get("references") or []) if isinstance(r, dict)]
        needing = [r for r in refs if ref_needs_enrichment_backfill(r)]
        if needing:
            candidates.append((eid, refs))
            refs_targeted += len(needing)
        if len(candidates) >= capped_limit:
            break

    if not candidates:
        return {
            "ticker": sym,
            "events_scanned": len(raw_events),
            "events_updated": 0,
            "refs_enriched": 0,
            "skipped": True,
            "reason": "no_refs_needing_enrichment",
        }

    patches: list[tuple[str, dict[str, Any]]] = []
    refs_enriched = 0
    refs_discarded = 0
    errors = 0

    for event_id, refs in candidates:
        try:
            stored = get_event(event_id)
            if not stored:
                continue
            structured = dict(stored.get("structured_summary") or {})
            em = dict(structured.get("event_meta") or {})
            stored_refs = [dict(r) for r in (em.get("references") or []) if isinstance(r, dict)]
            if not stored_refs:
                stored_refs = refs

            updated_refs: list[dict[str, Any]] = []
            event_changed = False
            for ref in stored_refs:
                ref_out = dict(ref)
                if not ref_needs_enrichment_backfill(ref_out):
                    updated_refs.append(ref_out)
                    continue

                payload = pipeline_payload_from_event_ref(ref_out, event=stored)
                ctx = run_ref_pipeline(
                    payload,
                    ticker=sym,
                    through=MAINTAINER_THROUGH,
                    skip_if_prefiltered=True,
                )
                enriched = merge_pipeline_ref(ref_out, dict(ctx.ref))
                enriched.pop("_relevance_prefiltered", None)
                enriched.pop("_force_re_enrich", None)
                enriched.pop("_raw_html_meta_published", None)
                if ctx.trace_dicts():
                    enriched["pipeline_step_trace"] = ctx.trace_dicts()
                enriched["enrichment_backfill_at"] = datetime.now(timezone.utc).isoformat()

                if not ctx.should_continue:
                    refs_discarded += 1
                elif ref_has_enrichment_signal(enriched):
                    refs_enriched += 1

                if _meaningful_ref_changed(ref_out, enriched):
                    event_changed = True
                    ref_out = enriched
                updated_refs.append(ref_out)

            if event_changed:
                em["references"] = updated_refs
                patches.append((event_id, em))
        except Exception as exc:
            errors += 1
            logger.warning("enrichment backfill failed for %s: %s", event_id, exc)

    patched = patch_event_meta(patches) if patches else 0

    return {
        "ticker": sym,
        "events_scanned": len(raw_events),
        "events_candidates": len(candidates),
        "refs_targeted": refs_targeted,
        "events_updated": patched,
        "refs_enriched": refs_enriched,
        "refs_discarded": refs_discarded,
        "meta_patched": patched,
        "errors": errors,
        "lookback_days": capped_lookback,
        "limit": capped_limit,
    }
