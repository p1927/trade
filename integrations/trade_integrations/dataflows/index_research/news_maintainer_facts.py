"""Maintainer pass: backfill missing fact claims and LLM adjudication on event refs."""

from __future__ import annotations

import logging
from datetime import date, timedelta
from typing import Any

logger = logging.getLogger(__name__)


def _ref_needs_adjudication(ref: dict[str, Any]) -> bool:
    if not isinstance(ref, dict):
        return False
    adj = ref.get("adjudication")
    if isinstance(adj, dict) and adj.get("claims"):
        return False
    claims = ref.get("extracted_claims") or []
    return not claims


def _event_refs_needing_facts(event: dict[str, Any]) -> list[dict[str, Any]]:
    structured = event.get("structured_summary") if isinstance(event.get("structured_summary"), dict) else {}
    em = structured.get("event_meta") if isinstance(structured.get("event_meta"), dict) else {}
    refs = [r for r in (em.get("references") or []) if isinstance(r, dict)]
    if refs:
        return [dict(r) for r in refs if _ref_needs_adjudication(r)]
    title = str(event.get("title") or "")
    summary = str(event.get("content") or event.get("content_summary") or "")
    if title or summary:
        return [
            {
                "ref_id": str(event.get("event_id") or event.get("canonical_story_id") or ""),
                "title": title,
                "summary": summary,
                "url": str(event.get("url") or ""),
                "published_at": str(event.get("published_at") or ""),
            }
        ]
    return []


def run_fact_adjudication_backfill(
    *,
    ticker: str = "NIFTY",
    lookback_days: int = 90,
    limit: int = 50,
) -> dict[str, Any]:
    """Enrich event references with claims + optional LLM adjudication."""
    from trade_integrations.dataflows.company_research.market import india_trading_date_iso
    from trade_integrations.dataflows.index_research.news_llm_story_pipeline import (
        adjudication_summary_from_refs,
        llm_adjudication_enabled,
        llm_adjudicate_refs,
        pre_enrich_refs_for_adjudication,
    )
    from trade_integrations.hub_storage.news_events_store import (
        get_event,
        list_events,
        patch_event_meta,
    )
    from trade_integrations.hub_storage.news_staging_store import pipeline_pause_status

    sym = ticker.strip().upper()
    pause = pipeline_pause_status(ticker=sym)
    if pause.get("pipeline_paused"):
        return {
            "ticker": sym,
            "skipped": True,
            "pipeline_paused": True,
            "pause_reason": str(pause.get("pause_reason") or ""),
        }

    end = date.fromisoformat(india_trading_date_iso()[:10])
    since = (end - timedelta(days=max(lookback_days, 1))).isoformat()
    raw_events = list_events(ticker=sym, since=since, limit=5000, include_rejected=False)

    candidates: list[tuple[str, list[dict[str, Any]]]] = []
    for event in raw_events:
        eid = str(event.get("event_id") or "").strip()
        if not eid:
            continue
        refs = _event_refs_needing_facts(event)
        if refs:
            candidates.append((eid, refs))
        if len(candidates) >= limit:
            break

    if not candidates:
        return {
            "ticker": sym,
            "events_scanned": len(raw_events),
            "events_updated": 0,
            "refs_adjudicated": 0,
            "skipped": True,
            "reason": "no_refs_needing_facts",
        }

    frame = None
    try:
        from trade_integrations.dataflows.index_research.sources.history_loader import (
            load_aligned_factor_history,
        )

        frame, _ = load_aligned_factor_history(days=120)
    except Exception as exc:
        logger.debug("fact backfill factor frame unavailable: %s", exc)

    patches: list[tuple[str, dict[str, Any]]] = []
    refs_adjudicated = 0
    errors = 0

    for event_id, refs in candidates:
        try:
            stored = get_event(event_id)
            if not stored:
                continue
            structured = dict(stored.get("structured_summary") or {})
            em = dict(structured.get("event_meta") or {})
            existing_refs = [dict(r) for r in (em.get("references") or []) if isinstance(r, dict)]

            enriched = pre_enrich_refs_for_adjudication(refs)
            if llm_adjudication_enabled():
                verdicts, _stats = llm_adjudicate_refs(enriched, market_context=None, frame=frame)
                by_id = {v.ref_id: v for v in verdicts}
                for ref in enriched:
                    rid = str(ref.get("ref_id") or "")
                    verdict = by_id.get(rid)
                    if verdict:
                        ref["adjudication"] = {
                            "ref_id": verdict.ref_id,
                            "claims": verdict.claims,
                            "tape_alignment": verdict.tape_alignment,
                            "credibility": verdict.credibility,
                            "discard": verdict.discard,
                            "discard_reason": verdict.discard_reason,
                            "story_fingerprint": verdict.story_fingerprint,
                            "source": verdict.source,
                        }
                        refs_adjudicated += 1
            else:
                refs_adjudicated += sum(1 for ref in enriched if ref.get("extracted_claims"))

            if existing_refs:
                enriched_by_url = {
                    str(r.get("url") or r.get("ref_id") or ""): r for r in enriched if r.get("url") or r.get("ref_id")
                }
                merged_refs: list[dict[str, Any]] = []
                for ref in existing_refs:
                    key = str(ref.get("url") or ref.get("ref_id") or "")
                    if key in enriched_by_url:
                        merged_refs.append({**ref, **enriched_by_url[key]})
                    else:
                        merged_refs.append(ref)
                em["references"] = merged_refs
            else:
                em["references"] = enriched

            em["adjudication_summary"] = adjudication_summary_from_refs(em.get("references") or enriched)
            structured["event_meta"] = em
            patches.append((event_id, em))
        except Exception as exc:
            errors += 1
            logger.warning("fact backfill failed for %s: %s", event_id, exc)

    patched = patch_event_meta(patches) if patches else 0

    return {
        "ticker": sym,
        "events_scanned": len(raw_events),
        "events_candidates": len(candidates),
        "events_updated": patched,
        "refs_adjudicated": refs_adjudicated,
        "meta_patched": patched,
        "errors": errors,
    }
