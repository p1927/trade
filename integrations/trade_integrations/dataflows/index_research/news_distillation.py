"""Distill one or more source refs into a hub news event narrative via MiniMax."""

from __future__ import annotations

import json
import logging
import os
import re
import uuid
from datetime import datetime, timezone
from typing import Any

_THINK_BLOCK = re.compile(
    r"<\s*(?:redacted_)?think(?:ing)?\s*>.*?<\s*/\s*(?:redacted_)?think(?:ing)?\s*>",
    re.DOTALL | re.IGNORECASE,
)
_THINK_OPEN = re.compile(r"<\s*(?:redacted_)?think(?:ing)?\s*>.*", re.DOTALL | re.IGNORECASE)

from trade_integrations.dataflows.index_research.news_enrichment import (
    build_content_summary,
    build_structured_summary,
    de_clickbait_title,
)
from trade_integrations.hub_storage.news_staging_store import require_minimax_for_distillation

logger = logging.getLogger(__name__)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _reference_from_row(row: dict[str, Any]) -> dict[str, Any]:
    sources = row.get("sources") if isinstance(row.get("sources"), list) else []
    first = sources[0] if sources else {}
    publisher = str(first.get("publisher") or row.get("source") or "").strip()
    if not publisher or publisher.lower() == "unknown":
        publisher = str(row.get("source") or first.get("vendor") or "unknown")
    return {
        "url": str(row.get("url") or first.get("url") or ""),
        "publisher": publisher,
        "vendor": str(first.get("vendor") or row.get("source") or publisher),
        "raw_title": str(row.get("title") or "")[:300],
        "raw_summary": str(row.get("summary") or "")[:600],
        "published_at": str(row.get("published_at") or ""),
        "fetched_at": _now_iso(),
        "extracted_claims": list(row.get("extracted_claims") or []),
    }


def _consensus_from_refs(
    refs: list[dict[str, Any]],
    *,
    tags: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Lightweight consensus block — no extra LLM call."""
    themes = list((tags or {}).get("themes") or [])
    bulls = [t for t in themes if t in {"rally", "recovery", "record_high"}]
    bears = [t for t in themes if t in {"crash", "selloff", "record_low"}]
    if bulls and bears:
        direction = "mixed"
    elif bears:
        direction = "bearish"
    elif bulls:
        direction = "bullish"
    elif "flat" in themes:
        direction = "flat"
    else:
        direction = "neutral"
    publishers = []
    seen = set()
    for ref in refs:
        pub = str(ref.get("publisher") or ref.get("vendor") or "").strip()
        if pub and pub.lower() not in seen:
            seen.add(pub.lower())
            publishers.append(pub)
    return {
        "direction": direction,
        "publish_day": (tags or {}).get("publish_day"),
        "topics": list((tags or {}).get("topics") or [])[:5],
        "factors": list((tags or {}).get("factors") or [])[:5],
        "publishers": publishers[:10],
        "ref_count": len(refs),
    }


def strip_minimax_thinking(text: str | None) -> str:
    """Remove MiniMax chain-of-thought blocks from model output."""
    if not text:
        return ""
    cleaned = _THINK_BLOCK.sub("", text).strip()
    cleaned = _THINK_OPEN.sub("", cleaned).strip()
    return cleaned


def is_distillation_leak(text: str | None) -> bool:
    """True when summary text still contains model reasoning artifacts."""
    if not text:
        return False
    lowered = text.lower()
    return (
        "<think" in lowered
        or "redacted_thinking" in lowered
        or lowered.startswith("the user wants me to")
    )


def _parse_llm_distill(text: str) -> dict[str, str]:
    text = strip_minimax_thinking(text)
    title = ""
    summary = ""
    if "<title>" in text and "</title>" in text:
        title = text.split("<title>")[-1].split("</title>")[0].strip()
    if "<summary>" in text and "</summary>" in text:
        summary = text.split("<summary>")[-1].split("</summary>")[0].strip()
    if not title and not summary:
        try:
            payload = json.loads(text[text.find("{") : text.rfind("}") + 1])
            title = str(payload.get("title") or "")
            summary = str(payload.get("content") or payload.get("summary") or "")
        except (json.JSONDecodeError, ValueError):
            summary = text.strip()[:600]
    title = strip_minimax_thinking(title)
    summary = strip_minimax_thinking(summary)
    return {"title": title[:300], "content": summary[:2000]}


def _distill_max_tokens() -> int:
    try:
        return max(200, int(os.getenv("MINIMAX_DISTILL_MAX_TOKENS", "800")))
    except ValueError:
        return 800


def _call_distill_model(
    prompt: str,
    *,
    max_tokens: int,
    reasoning_split: bool,
) -> dict[str, str]:
    from trade_integrations.nse_browser.minimax_agent import chat_completions_create, _model

    kwargs: dict[str, Any] = {
        "model": _model(),
        "messages": [{"role": "user", "content": prompt[:12000]}],
        "max_tokens": max_tokens,
        "temperature": 0.1,
    }
    if reasoning_split:
        kwargs["extra_body"] = {"reasoning_split": True}
    response = chat_completions_create(**kwargs)
    text = str(response.choices[0].message.content or "")
    return _parse_llm_distill(text)


def _llm_distill(
    *,
    previous_title: str,
    previous_content: str,
    refs: list[dict[str, Any]],
) -> dict[str, str]:
    """Always call MiniMax to distill/update the event narrative."""
    require_minimax_for_distillation()

    context_lines = []
    for ref in refs[-8:]:
        context_lines.append(
            f"title: {ref.get('raw_title') or ref.get('title')}, "
            f"summary: {(ref.get('raw_summary') or ref.get('summary') or '')[:800]}"
        )
    previous_content = strip_minimax_thinking(previous_content)
    story = f"title: {previous_title}\nsummary: {previous_content}"
    prompt = (
        "You summarize financial market news for traders. Given prior story text in <story> "
        "and new source articles in <context>, update the story title and summary. "
        "Reconcile conflicting numbers as ranges. Do not invent facts not present in sources. "
        "Do not include reasoning or analysis steps. "
        "Output <title>...</title> and <summary>...</summary> only.\n\n"
        f"<story>\n{story}\n</story>\n\n<context>\n" + "\n".join(context_lines) + "\n</context>"
    )

    base_tokens = _distill_max_tokens()
    attempts: list[tuple[int, bool]] = [
        (base_tokens, True),
        (max(base_tokens + 400, 1200), True),
        (base_tokens, False),
    ]
    try:
        for max_tokens, reasoning_split in attempts:
            parsed = _call_distill_model(
                prompt,
                max_tokens=max_tokens,
                reasoning_split=reasoning_split,
            )
            if parsed.get("title") or parsed.get("content"):
                return parsed
            logger.warning(
                "MiniMax distillation empty parse (max_tokens=%s reasoning_split=%s)",
                max_tokens,
                reasoning_split,
            )
    except Exception as exc:
        raise RuntimeError(f"MiniMax distillation failed: {exc}") from exc

    fallback = _keyword_fallback(refs)
    if fallback.get("title") or fallback.get("content"):
        logger.warning("MiniMax distillation using keyword fallback after empty LLM output")
        return fallback
    raise RuntimeError("MiniMax distillation returned empty title and summary")


def _keyword_fallback(refs: list[dict[str, Any]]) -> dict[str, str]:
    """Used only when MiniMax returns an unparseable but non-empty response."""
    if not refs:
        return {"title": "", "content": ""}
    titles = [de_clickbait_title(str(r.get("raw_title") or r.get("title") or "")) for r in refs]
    bodies = [str(r.get("raw_summary") or r.get("summary") or "") for r in refs]
    title = max(titles, key=len) if titles else ""
    combined_body = " ".join(b for b in bodies if b.strip())
    content = build_content_summary(title, combined_body)
    return {"title": title[:300], "content": content[:2000]}


def _rule_fallback_distill(
    *,
    refs: list[dict[str, Any]],
    previous: dict[str, Any] | None = None,
) -> dict[str, str]:
    """Deterministic distillation when MiniMax is unavailable."""
    from trade_integrations.dataflows.index_research.news_claim_extraction import extract_claims

    normalized = [_reference_from_row(r) for r in refs if r.get("title") or r.get("summary")]
    base = _keyword_fallback(normalized)
    claim_lines: list[str] = []
    for ref in normalized[-6:]:
        for claim in extract_claims(
            str(ref.get("raw_title") or ref.get("title") or ""),
            str(ref.get("raw_summary") or ref.get("summary") or ""),
        ):
            kind = claim.get("kind")
            value = claim.get("value")
            status = claim.get("status") or "claimed"
            claim_lines.append(f"- [{status}] {kind}: {value}")

    facts = base.get("content") or ""
    if claim_lines:
        facts = "Facts (claimed):\n" + "\n".join(claim_lines[:12])
    impact = "Impact (claimed): Market reaction not yet verified against factor panel."
    if previous:
        prev = strip_minimax_thinking(str(previous.get("content_summary") or previous.get("content") or ""))
        if prev:
            facts = f"{facts}\n\nPrior narrative:\n{prev[:800]}"
    content = f"{facts}\n\n{impact}"[:2000]
    title = base.get("title") or str((previous or {}).get("title") or "Market update")[:300]
    return {"title": title, "content": content, "distillation_mode": "rule_fallback"}


def distill_event(
    *,
    refs: list[dict[str, Any]],
    previous: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build distilled title, content, timeline entry, and references list."""
    from trade_integrations.hub_storage.news_staging_store import minimax_configured, rule_fallback_distillation_enabled

    normalized_refs = [_reference_from_row(r) for r in refs if r.get("title") or r.get("summary")]
    for ref in normalized_refs:
        from trade_integrations.dataflows.index_research.news_claim_extraction import extract_claims

        if not ref.get("extracted_claims"):
            ref["extracted_claims"] = extract_claims(
                str(ref.get("raw_title") or ref.get("title") or ""),
                str(ref.get("raw_summary") or ref.get("summary") or ""),
            )

    prev_title = str((previous or {}).get("title") or "")
    prev_content = strip_minimax_thinking(
        str((previous or {}).get("content_summary") or (previous or {}).get("content") or "")
    )

    distilled_by = "minimax"
    if minimax_configured():
        try:
            llm_out = _llm_distill(
                previous_title=prev_title,
                previous_content=prev_content,
                refs=normalized_refs,
            )
        except Exception as exc:
            logger.warning("MiniMax distillation failed, using rule fallback: %s", exc)
            llm_out = _rule_fallback_distill(refs=normalized_refs, previous=previous)
            distilled_by = "rule_fallback"
    elif rule_fallback_distillation_enabled():
        llm_out = _rule_fallback_distill(refs=normalized_refs, previous=previous)
        distilled_by = "rule_fallback"
    else:
        raise RuntimeError("MiniMax not configured and rule-fallback distillation is disabled")

    title = llm_out.get("title") or ""
    content = llm_out.get("content") or ""
    if is_distillation_leak(title) or is_distillation_leak(content):
        fallback = _keyword_fallback(normalized_refs)
        title = fallback["title"] if is_distillation_leak(title) else title
        content = fallback["content"] if is_distillation_leak(content) else content
    if not title or not content:
        fallback = _keyword_fallback(normalized_refs)
        title = title or fallback["title"]
        content = content or fallback["content"]

    latest_ref = normalized_refs[-1] if normalized_refs else {}
    pub = str(latest_ref.get("publisher") or "unknown")
    raw_title = str(latest_ref.get("raw_title") or "")
    latest_url = str(latest_ref.get("url") or "")
    from trade_integrations.dataflows.index_research.news_parent_events import (
        infer_event_kind,
        infer_parent_event_id,
        infer_provenance,
        infer_scope,
    )

    parent_id = infer_parent_event_id(latest_ref if latest_ref else {})
    timeline_kind = "created" if not previous else "update"
    if parent_id and previous:
        timeline_kind = "update"
    timeline_entry = {
        "at": _now_iso(),
        "kind": timeline_kind,
        "publisher": pub,
        "raw_title": raw_title[:180],
        "summary": f"Source {pub}: {raw_title[:180]}",
        "ref_urls": [r.get("url") for r in normalized_refs if r.get("url")][-5:],
    }
    if latest_url and latest_url not in timeline_entry["ref_urls"]:
        timeline_entry["ref_urls"].append(latest_url)

    tags = ((previous or {}).get("tags") if previous else {}) or {}
    if not tags and refs:
        from trade_integrations.dataflows.index_research.news_tags import build_article_tags

        first = refs[0]
        tags = build_article_tags(
            str(first.get("title") or first.get("raw_title") or ""),
            str(first.get("summary") or first.get("raw_summary") or ""),
        ).to_dict()

    structured = build_structured_summary(title, content)
    prior_meta = ((previous.get("structured_summary") or {}).get("event_meta") or {}) if previous else {}
    event_id = str(prior_meta.get("event_id") or "").strip() or str(uuid.uuid4())
    event_meta = {
        "event_id": event_id,
        "distilled": True,
        "distilled_by": distilled_by,
        "distillation_mode": distilled_by,
        "parent_event_id": parent_id,
        "event_kind": infer_event_kind(latest_ref if latest_ref else {}),
        "scope": infer_scope(latest_ref if latest_ref else {}),
        "provenance": infer_provenance(latest_ref if latest_ref else {}),
        "market_impact_status": "claimed" if distilled_by == "rule_fallback" else "unverified",
        "references": normalized_refs,
        "timeline": [timeline_entry],
        "ref_count": len(normalized_refs),
        "consensus": _consensus_from_refs(normalized_refs, tags=tags),
    }
    if previous:
        prior_timeline = list(prior_meta.get("timeline") or [])
        prior_refs = list(prior_meta.get("references") or [])
        seen_urls = {r.get("url") for r in prior_refs}
        for ref in normalized_refs:
            if ref.get("url") not in seen_urls:
                prior_refs.append(ref)
        prior_timeline.append(timeline_entry)
        event_meta["references"] = prior_refs[-20:]
        event_meta["timeline"] = prior_timeline[-30:]
        event_meta["ref_count"] = len(prior_refs)
        event_meta["consensus"] = _consensus_from_refs(prior_refs, tags=tags or ((previous.get("tags") or {})))

    return {
        "title": title,
        "content": content,
        "structured_summary": {
            "facts": structured.facts,
            "entities": structured.entities,
            "implied_factors": structured.implied_factors,
            "event_meta": event_meta,
        },
        "timeline_entry": timeline_entry,
    }
