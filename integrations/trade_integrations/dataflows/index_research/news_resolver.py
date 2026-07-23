"""Tiered T0–T3 resolver for hub news staging refs — enrich, create, or discard."""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from trade_integrations.dataflows.index_research.news_claim_extraction import extract_claims
from trade_integrations.dataflows.index_research.news_dedup import canonical_story_id, publish_day_from_value
from trade_integrations.dataflows.index_research.news_event_matching import (
    enhanced_summary_similarity,
    find_matching_event,
)
from trade_integrations.dataflows.index_research.news_parent_events import infer_parent_event_id
from trade_integrations.dataflows.index_research.news_relevance import (
    assess_ref_relevance,
    relevance_min_confidence,
)
from trade_integrations.dataflows.index_research.news_tags import build_article_tags
from trade_integrations.hub_storage.news_event_models import DistilledNewsEvent, NewsReference, TimelineEntry
from trade_integrations.hub_storage.news_events_store import get_event, upsert_event
from trade_integrations.hub_storage.news_staging_store import _url_dedupe_key, mark_ref_discarded

logger = logging.getLogger(__name__)

_MATERIAL_CLAIM_KINDS = frozenset({"percent_move", "index_level", "rate_change_bps"})


@dataclass
class ResolveDecision:
    action: str
    event_id: str = ""
    matched_event: dict[str, Any] | None = None
    reason: str = ""
    tier: str = ""
    attach_only: bool = False
    match_score: float = 0.0
    enriched_refs: list[dict[str, Any]] = field(default_factory=list)
    wiki_hit: dict[str, Any] | None = None
    adjudication_summary: dict[str, Any] | None = None


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _claim_keys(claims: list[dict[str, Any]]) -> set[str]:
    out: set[str] = set()
    for claim in claims or []:
        if not isinstance(claim, dict):
            continue
        kind = str(claim.get("kind") or "")
        value = claim.get("value")
        if kind:
            out.add(f"{kind}:{value}")
    return out


def _event_claim_keys(event: dict[str, Any]) -> set[str]:
    keys: set[str] = set()
    structured = event.get("structured_summary") or {}
    if isinstance(structured, dict):
        for fact in structured.get("facts") or []:
            if isinstance(fact, str) and fact.strip():
                keys.add(f"fact:{fact.strip()[:120]}")
        em = structured.get("event_meta") or {}
        if isinstance(em, dict):
            for ref in em.get("references") or []:
                if isinstance(ref, dict):
                    keys |= _claim_keys(
                        extract_claims(
                            str(ref.get("raw_title") or ref.get("title") or ""),
                            str(ref.get("raw_summary") or ref.get("summary") or ""),
                        )
                    )
    keys |= _claim_keys(
        extract_claims(
            str(event.get("title") or ""),
            str(event.get("content_summary") or event.get("content") or ""),
        )
    )
    return keys


def ref_adds_new_claims(ref: dict[str, Any], event: dict[str, Any]) -> bool:
    new_keys = _claim_keys(extract_claims(str(ref.get("title") or ""), str(ref.get("summary") or "")))
    if not new_keys:
        return False
    return bool(new_keys - _event_claim_keys(event))


def needs_full_redistill(ref: dict[str, Any], event: dict[str, Any]) -> bool:
    """Revision path — material new claims warrant full MiniMax distill."""
    new_claims = extract_claims(str(ref.get("title") or ""), str(ref.get("summary") or ""))
    prior = _event_claim_keys(event)
    delta = _claim_keys(new_claims) - prior
    if not delta:
        return False
    for key in delta:
        kind = key.split(":", 1)[0]
        if kind in _MATERIAL_CLAIM_KINDS:
            return True
    return False


def url_already_in_event(ref: dict[str, Any], event: dict[str, Any]) -> bool:
    url_key = _url_dedupe_key(str(ref.get("url") or ""))
    if not url_key:
        return False
    for src in event.get("sources") or []:
        if isinstance(src, dict) and _url_dedupe_key(str(src.get("url") or "")) == url_key:
            return True
    structured = event.get("structured_summary") or {}
    em = (structured.get("event_meta") or {}) if isinstance(structured, dict) else {}
    for existing in em.get("references") or []:
        if isinstance(existing, dict) and _url_dedupe_key(str(existing.get("url") or "")) == url_key:
            return True
    if _url_dedupe_key(str(event.get("url") or "")) == url_key:
        return True
    return False


def filter_relevant_refs(
    refs: list[dict[str, Any]],
    *,
    ticker: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """T0 relevance gate — return kept refs and auto-discarded rows."""
    sym = ticker.strip().upper()
    kept: list[dict[str, Any]] = []
    discarded: list[dict[str, Any]] = []
    for candidate in refs:
        verdict = assess_ref_relevance(candidate, ticker=sym)
        if not verdict.relevant and verdict.confidence >= relevance_min_confidence():
            rid = str(candidate.get("ref_id") or "")
            if rid:
                mark_ref_discarded(
                    rid,
                    reason=verdict.reason or "irrelevant",
                    relevance=verdict.to_dict(),
                    restore_payload=dict(candidate),
                    source_kind="auto_gate",
                )
            discarded.append({"ref_id": rid, "reason": verdict.reason})
            continue
        kept.append(candidate)
    return kept, discarded


def _pick_primary_ref(refs: list[dict[str, Any]]) -> dict[str, Any]:
    if not refs:
        return {}
    return max(refs, key=lambda r: len(str(r.get("summary") or r.get("title") or "")))


def _list_match_candidates(
    *,
    ticker: str,
    publish_day: str | None,
    parent_event_id: str | None = None,
) -> list[dict[str, Any]]:
    from trade_integrations.dataflows.index_research.news_parent_events import event_parent_id
    from trade_integrations.hub_storage.news_events_store import distilled_event_to_headline_dict, list_events

    sym = ticker.strip().upper()
    try:
        from trade_integrations.hub_storage.news_event_index import (
            ensure_event_index,
            query_index_candidates,
        )

        ensure_event_index(ticker=sym)
        indexed = query_index_candidates(
            ticker=sym,
            publish_day=publish_day or None,
            parent_event_id=parent_event_id,
            limit=120 if parent_event_id else 80,
        )
        if indexed:
            if parent_event_id:
                return [
                    row
                    for row in indexed
                    if event_parent_id(row) == parent_event_id or not event_parent_id(row)
                ]
            return indexed
    except Exception as exc:
        logger.debug("resolver index candidates fallback: %s", exc)

    if parent_event_id:
        events = list_events(ticker=sym, limit=120, include_rejected=False)
        return [
            distilled_event_to_headline_dict(event)
            for event in events
            if event_parent_id(event) == parent_event_id or not event_parent_id(event)
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


def _resolve_wiki_match(
    ref: dict[str, Any],
    *,
    ticker: str,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    try:
        from trade_integrations.dataflows.hub_wiki.search_dedup import find_wiki_match_for_record
        from trade_integrations.hub_storage.news_events_store import distilled_event_to_headline_dict
        from trade_integrations.hub_storage.news_pipeline_config import load_news_pipeline_config

        pipe_cfg = load_news_pipeline_config()
        if not pipe_cfg.wiki_search_enabled:
            return None, None
        wiki_hit = find_wiki_match_for_record(
            ref,
            ticker=ticker,
            top_k=pipe_cfg.wiki_search_top_k,
            min_score=pipe_cfg.wiki_search_min_score,
        )
        if not wiki_hit:
            return None, None
        stored = get_event(str(wiki_hit.get("event_id") or ""))
        if not stored:
            return None, None
        candidate = distilled_event_to_headline_dict(stored)
        if find_matching_event(ref, [candidate], ticker=ticker):
            return candidate, wiki_hit
    except Exception as exc:
        logger.debug("wiki resolver match skipped: %s", exc)
    return None, None


def _match_score(ref: dict[str, Any], event: dict[str, Any]) -> float:
    ref_text = f"{ref.get('title') or ''} {ref.get('summary') or ''}"
    event_text = f"{event.get('title') or ''} {event.get('content_summary') or ''}"
    return enhanced_summary_similarity(ref_text, event_text)


def _find_gray_zone_candidate(
    ref: dict[str, Any],
    candidates: list[dict[str, Any]],
    *,
    ticker: str,
) -> tuple[dict[str, Any] | None, float]:
    """Weak match at gray_low threshold but not at match_threshold (same rule gates + boosts)."""
    from trade_integrations.dataflows.index_research.news_event_matching import match_threshold
    from trade_integrations.dataflows.index_research.news_resolver_agent import resolver_agent_gray_low

    gray_high = match_threshold()
    gray_low = resolver_agent_gray_low()
    if find_matching_event(ref, candidates, ticker=ticker, threshold=gray_high):
        return None, 0.0
    weak = find_matching_event(ref, candidates, ticker=ticker, threshold=gray_low)
    if not weak:
        return None, 0.0
    return weak, _match_score(ref, weak)


def resolve_staging_group(
    refs: list[dict[str, Any]],
    *,
    ticker: str | None = None,
    market_context: dict[str, Any] | None = None,
    adjudication_summary: dict[str, Any] | None = None,
    t4_budget: dict[str, int] | None = None,
) -> ResolveDecision:
    """Run T0–T4 gates and return enrich | create | discard | skip_duplicate_url."""
    _ = market_context
    if not refs:
        return ResolveDecision(action="skip_empty", reason="empty_group")

    sym = (ticker or refs[-1].get("ticker") or "NIFTY").strip().upper()
    kept, _discarded = filter_relevant_refs(refs, ticker=sym)
    if not kept:
        return ResolveDecision(
            action="discard",
            reason="all_refs_irrelevant",
            tier="t0",
        )

    from trade_integrations.dataflows.index_research.news_claim_extraction import enrich_ref_with_claims
    from trade_integrations.dataflows.article_body import enrich_ref_summary_from_url

    enriched = [enrich_ref_with_claims(dict(r)) for r in kept]
    enriched = [enrich_ref_summary_from_url(r) for r in enriched]
    ref = _pick_primary_ref(enriched)

    if adjudication_summary is None:
        from trade_integrations.dataflows.index_research.news_llm_story_pipeline import (
            adjudication_summary_from_refs,
        )

        adjudication_summary = adjudication_summary_from_refs(enriched)

    publish_day = publish_day_from_value(str(ref.get("published_at") or ""))
    ref_tags = ref.get("tags") if isinstance(ref.get("tags"), dict) else {}
    if not ref_tags.get("topics"):
        ref_tags = build_article_tags(
            str(ref.get("title") or ""),
            str(ref.get("summary") or ""),
            ticker=sym,
            published_at=str(ref.get("published_at") or ""),
        ).to_dict()
        ref["tags"] = ref_tags
    parent_id = infer_parent_event_id(ref, tags=ref_tags)
    if parent_id:
        ref["parent_event_id"] = parent_id

    url_id = canonical_story_id(str(ref.get("title") or ""), str(ref.get("url") or ""))
    if url_id and get_event(url_id):
        return ResolveDecision(
            action="skip_duplicate_url",
            event_id=url_id,
            tier="t0",
            enriched_refs=enriched,
            adjudication_summary=adjudication_summary,
        )

    matched, wiki_hit = _resolve_wiki_match(ref, ticker=sym)
    tier = "t2" if matched and wiki_hit else ""
    candidates: list[dict[str, Any]] = []
    if not matched:
        candidates = _list_match_candidates(
            ticker=sym,
            publish_day=publish_day or None,
            parent_event_id=parent_id,
        )
        matched = find_matching_event(ref, candidates, ticker=sym)
        tier = "t1" if matched else ""

    if matched:
        if url_already_in_event(ref, matched):
            return ResolveDecision(
                action="discard",
                event_id=str(matched.get("canonical_story_id") or matched.get("event_id") or ""),
                matched_event=matched,
                reason="duplicate_url_in_event",
                tier="t0",
                enriched_refs=enriched,
                wiki_hit=wiki_hit,
                adjudication_summary=adjudication_summary,
            )
        if not ref_adds_new_claims(ref, matched):
            return ResolveDecision(
                action="discard",
                event_id=str(matched.get("canonical_story_id") or matched.get("event_id") or ""),
                matched_event=matched,
                reason="syndicated_no_new_claims",
                tier="t3",
                match_score=_match_score(ref, matched),
                enriched_refs=enriched,
                wiki_hit=wiki_hit,
                adjudication_summary=adjudication_summary,
            )
        event_id = str(matched.get("canonical_story_id") or matched.get("event_id") or "")
        attach_only = not needs_full_redistill(ref, matched)
        return ResolveDecision(
            action="enrich",
            event_id=event_id,
            matched_event=matched,
            reason="enrich_new_facts",
            tier=tier or "t3",
            attach_only=attach_only,
            match_score=_match_score(ref, matched),
            enriched_refs=enriched,
            wiki_hit=wiki_hit,
            adjudication_summary=adjudication_summary,
        )

    gray_candidate, gray_score = _find_gray_zone_candidate(ref, candidates, ticker=sym)
    from trade_integrations.dataflows.index_research.news_resolver_agent import (
        adjudicate_gray_zone,
        resolver_agent_enabled,
    )

    agent_chose_create = False
    if gray_candidate and (t4_budget is not None or resolver_agent_enabled()):
        if t4_budget is None:
            t4_budget = {"remaining": 1}

        remaining = int(t4_budget.get("remaining") or 0)
        if resolver_agent_enabled() and remaining > 0:
            t4_budget["remaining"] = remaining - 1
            agent = adjudicate_gray_zone(
                ref,
                candidate=gray_candidate,
                match_score=gray_score,
                ticker=sym,
            )
            agent_action = str(agent.get("action") or "").strip().lower()
            if agent_action == "discard":
                return ResolveDecision(
                    action="discard",
                    event_id=str(
                        gray_candidate.get("canonical_story_id")
                        or gray_candidate.get("event_id")
                        or ""
                    ),
                    matched_event=gray_candidate,
                    reason=str(agent.get("reason") or "agent_gray_discard"),
                    tier="t4",
                    match_score=gray_score,
                    enriched_refs=enriched,
                    adjudication_summary=adjudication_summary,
                )
            if agent_action == "enrich":
                event_id = str(agent.get("event_id") or "").strip() or str(
                    gray_candidate.get("canonical_story_id")
                    or gray_candidate.get("event_id")
                    or ""
                )
                if url_already_in_event(ref, gray_candidate):
                    return ResolveDecision(
                        action="discard",
                        event_id=event_id,
                        matched_event=gray_candidate,
                        reason="duplicate_url_in_event",
                        tier="t0",
                        enriched_refs=enriched,
                        adjudication_summary=adjudication_summary,
                    )
                attach_only = not needs_full_redistill(ref, gray_candidate)
                return ResolveDecision(
                    action="enrich",
                    event_id=event_id,
                    matched_event=gray_candidate,
                    reason=str(agent.get("reason") or "agent_gray_enrich"),
                    tier="t4",
                    attach_only=attach_only,
                    match_score=gray_score,
                    enriched_refs=enriched,
                    adjudication_summary=adjudication_summary,
                )
            if agent_action == "create":
                agent_chose_create = True

    if gray_candidate and not agent_chose_create:
        if not ref_adds_new_claims(ref, gray_candidate):
            return ResolveDecision(
                action="discard",
                event_id=str(
                    gray_candidate.get("canonical_story_id")
                    or gray_candidate.get("event_id")
                    or ""
                ),
                matched_event=gray_candidate,
                reason="gray_zone_syndicated",
                tier="t3",
                match_score=gray_score,
                enriched_refs=enriched,
                adjudication_summary=adjudication_summary,
            )
        gray_event_id = str(
            gray_candidate.get("canonical_story_id") or gray_candidate.get("event_id") or ""
        )
        attach_only = not needs_full_redistill(ref, gray_candidate)
        return ResolveDecision(
            action="enrich",
            event_id=gray_event_id,
            matched_event=gray_candidate,
            reason="gray_zone_rule_enrich",
            tier="t3",
            attach_only=attach_only,
            match_score=gray_score,
            enriched_refs=enriched,
            adjudication_summary=adjudication_summary,
        )

    event_id = url_id or canonical_story_id(str(ref.get("title") or ""), str(ref.get("url") or ""))
    return ResolveDecision(
        action="create",
        event_id=event_id,
        reason="no_match",
        tier="t3",
        attach_only=False,
        enriched_refs=enriched,
        adjudication_summary=adjudication_summary,
    )


def _normalize_ref_for_event(ref: dict[str, Any]) -> dict[str, Any]:
    url = str(ref.get("url") or "")
    return {
        "ref_id": str(ref.get("ref_id") or ""),
        "url": url,
        "publisher": str(ref.get("source") or ref.get("publisher") or "unknown"),
        "vendor": str(ref.get("source") or "unknown"),
        "raw_title": str(ref.get("title") or "")[:500],
        "raw_summary": str(ref.get("summary") or "")[:2000],
        "published_at": str(ref.get("published_at") or ""),
        "fetched_at": _now_iso(),
        "extracted_claims": list(ref.get("extracted_claims") or extract_claims(
            str(ref.get("title") or ""), str(ref.get("summary") or "")
        )),
    }


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


def attach_refs_to_event(
    *,
    refs: list[dict[str, Any]],
    event_id: str,
    ticker: str,
) -> dict[str, Any]:
    """Attach-only enrich — timeline + references without MiniMax re-distill."""
    stored = get_event(event_id)
    if not stored:
        return {"ok": False, "reason": "event_missing", "event_id": event_id}

    event = DistilledNewsEvent.from_dict(stored)
    structured = dict(event.structured_summary or {})
    em = dict(structured.get("event_meta") or {})
    refs_meta = list(em.get("references") or [])
    timeline_meta = list(em.get("timeline") or [])
    seen_urls = {_url_dedupe_key(str(r.get("url") or "")) for r in refs_meta if isinstance(r, dict)}

    attached = 0
    for ref in refs:
        norm = _normalize_ref_for_event(ref)
        url_key = _url_dedupe_key(norm.get("url") or "")
        if url_key and url_key in seen_urls:
            continue
        seen_urls.add(url_key)
        refs_meta.append(norm)
        timeline_meta.append(
            {
                "at": _now_iso(),
                "kind": "update",
                "summary": str(ref.get("summary") or ref.get("title") or "")[:200],
                "publisher": norm.get("publisher"),
                "raw_title": norm.get("raw_title"),
                "ref_urls": [norm["url"]] if norm.get("url") else [],
            }
        )
        event.references.append(NewsReference.from_dict(norm))
        event.timeline.append(
            TimelineEntry(
                at=_now_iso(),
                kind="update",
                summary=str(ref.get("summary") or ref.get("title") or "")[:200],
                publisher=str(norm.get("publisher") or ""),
                raw_title=str(norm.get("raw_title") or "")[:180],
                ref_urls=[norm["url"]] if norm.get("url") else [],
            )
        )
        src = ref.get("sources") if isinstance(ref.get("sources"), list) else []
        if not src and norm.get("url"):
            src = [
                {
                    "vendor": norm.get("vendor") or "unknown",
                    "publisher": norm.get("publisher") or "unknown",
                    "url": norm.get("url"),
                    "fetched_at": _now_iso(),
                }
            ]
        event.sources = _merge_sources(event.sources, src)
        attached += 1

    if attached == 0:
        return {"ok": True, "attached": 0, "reason": "duplicate_urls", "event_id": event_id}

    em["references"] = refs_meta[-20:]
    em["timeline"] = timeline_meta[-30:]
    em["ref_count"] = len(refs_meta)
    structured["event_meta"] = em
    event.structured_summary = structured
    event.updated_at = _now_iso()
    upsert_event(event)
    return {"ok": True, "attached": attached, "event_id": event_id, "attach_only": True}


def staging_pending_ttl_days() -> int:
    raw = os.getenv("HUB_NEWS_STAGING_TTL_DAYS", "7").strip()
    try:
        return max(1, int(raw))
    except ValueError:
        return 7


def purge_stale_pending_refs(
    *,
    ticker: str | None = None,
    only_when_wiki_blocked: bool = True,
) -> dict[str, Any]:
    """Drop queued staging refs older than TTL (default when wiki blocks ingest)."""
    if only_when_wiki_blocked:
        try:
            from trade_integrations.dataflows.hub_wiki.probe import ingest_blocked_by_wiki

            if not ingest_blocked_by_wiki():
                return {"purged": 0, "skipped": True, "reason": "wiki_ok"}
        except Exception:
            pass

    from trade_integrations.hub_storage.news_staging_store import mark_ref_discarded

    ttl_days = staging_pending_ttl_days()
    ttl_seconds = ttl_days * 86400
    sym = (ticker or "").strip().upper()

    from trade_integrations.hub_storage import news_staging_store as staging_store

    pending = staging_store.list_pending_refs(ticker=ticker, limit=10_000)
    purged = 0
    for ref in pending:
        created = str(ref.get("created_at") or ref.get("published_at") or "")
        age = staging_store._parse_iso_age_seconds(created)
        if age is None or age < ttl_seconds:
            continue
        if sym and str(ref.get("ticker") or "").upper() != sym:
            continue
        rid = str(ref.get("ref_id") or "")
        if not rid:
            continue
        mark_ref_discarded(
            rid,
            reason="staging_ttl_expired",
            restore_payload=dict(ref),
            source_kind="ttl",
        )
        purged += 1
    return {"purged": purged, "ttl_days": ttl_days}
