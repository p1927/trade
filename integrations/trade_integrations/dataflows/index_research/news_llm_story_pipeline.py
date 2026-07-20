"""Fact-first LLM story pipeline: adjudicate refs, group by fingerprint, then distill."""

from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import asdict, dataclass, field
from typing import Any

from trade_integrations.dataflows.index_research.news_dedup import (
    publish_day_from_value,
    semantic_cluster_key,
)
from trade_integrations.dataflows.index_research.news_distillation import (
    call_minimax_json_text,
    strip_minimax_thinking,
)
from trade_integrations.dataflows.index_research.news_market_context import (
    format_market_context_for_prompt,
)
from trade_integrations.hub_storage.news_staging_store import require_minimax_for_distillation

logger = logging.getLogger(__name__)

_JSON_ARRAY = re.compile(r"\[\s*(?:\{.*\}|\])\s*\]", re.DOTALL)

_TAPE_ALIGNMENTS = frozenset({"supported", "exaggerated", "contradicted", "unverifiable"})
_CREDIBILITY = frozenset({"valid", "exaggeration", "likely_hoax", "irrelevant"})


@dataclass
class AdjudicationVerdict:
    ref_id: str
    claims: list[dict[str, Any]] = field(default_factory=list)
    tape_alignment: str = "unverifiable"
    credibility: str = "valid"
    discard: bool = False
    discard_reason: str = ""
    story_fingerprint: str = ""
    source: str = "llm"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _ref_id(ref: dict[str, Any], index: int) -> str:
    rid = str(ref.get("ref_id") or "").strip()
    if rid:
        return rid
    return f"idx:{index}"


def _ref_summary_line(ref: dict[str, Any], ref_id: str) -> str:
    title = str(ref.get("title") or "")[:200]
    summary = str(ref.get("summary") or "")[:600]
    url = str(ref.get("url") or "")[:200]
    pub = str(ref.get("published_at") or "")[:32]
    publisher = ""
    sources = ref.get("sources")
    if isinstance(sources, list) and sources:
        publisher = str((sources[0] or {}).get("publisher") or "")
    claims = ref.get("extracted_claims") or ref.get("adjudication", {}).get("claims") or []
    claim_hint = ""
    if isinstance(claims, list) and claims:
        bits = [str(c.get("value") or c.get("text") or "")[:60] for c in claims[:3]]
        claim_hint = " | claims=" + "; ".join(b for b in bits if b)
    return (
        f"id={ref_id} | pub={pub} | publisher={publisher} | "
        f"title={title} | summary={summary}{claim_hint} | url={url}"
    )


def _parse_json_array(text: str) -> list[Any]:
    text = strip_minimax_thinking(text)
    if not text:
        return []
    candidates: list[str] = []
    match = _JSON_ARRAY.search(text)
    if match:
        candidates.append(match.group(0))
    start = text.find("[")
    end = text.rfind("]")
    if start >= 0 and end > start:
        candidates.append(text[start : end + 1])
    seen: set[str] = set()
    for blob in candidates:
        if blob in seen:
            continue
        seen.add(blob)
        try:
            payload = json.loads(blob)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, list):
            return payload
    return []


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)).strip())
    except ValueError:
        return default


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name, "1" if default else "0").strip().lower()
    return raw in {"1", "true", "yes", "on"}


def llm_adjudication_enabled() -> bool:
    try:
        from trade_integrations.hub_storage.news_pipeline_config import load_news_pipeline_config

        return bool(load_news_pipeline_config().llm_adjudication_enabled)
    except Exception:
        return _env_bool("HUB_NEWS_LLM_ADJUDICATION_ENABLED", True)


def adjudication_batch_size() -> int:
    try:
        from trade_integrations.hub_storage.news_pipeline_config import load_news_pipeline_config

        return int(load_news_pipeline_config().llm_adjudication_batch_size)
    except Exception:
        return _env_int("HUB_NEWS_LLM_ADJUDICATION_BATCH_SIZE", 8)


def adjudication_chunk_size() -> int:
    try:
        return _env_int("HUB_NEWS_ADJUDICATION_CHUNK_SIZE", 4)
    except ValueError:
        return 4


def adjudication_max_tokens() -> int:
    try:
        from trade_integrations.hub_storage.news_pipeline_config import load_news_pipeline_config

        return int(load_news_pipeline_config().adjudication_max_tokens)
    except Exception:
        return _env_int("MINIMAX_ADJUDICATION_MAX_TOKENS", 8192)


def story_dedup_max_tokens() -> int:
    try:
        from trade_integrations.hub_storage.news_pipeline_config import load_news_pipeline_config

        return int(load_news_pipeline_config().story_dedup_max_tokens)
    except Exception:
        return _env_int("MINIMAX_STORY_DEDUP_MAX_TOKENS", 8192)


def discard_contradicted_enabled() -> bool:
    try:
        from trade_integrations.hub_storage.news_pipeline_config import load_news_pipeline_config

        return bool(load_news_pipeline_config().adjudication_discard_contradicted)
    except Exception:
        return _env_bool("HUB_NEWS_ADJUDICATION_DISCARD_CONTRADICTED", True)


def discard_hoax_enabled() -> bool:
    try:
        from trade_integrations.hub_storage.news_pipeline_config import load_news_pipeline_config

        return bool(load_news_pipeline_config().adjudication_discard_hoax)
    except Exception:
        return _env_bool("HUB_NEWS_ADJUDICATION_DISCARD_HOAX", True)


def _token_attempts(base: int) -> tuple[int, ...]:
    return (base, base + 4096, base + 8192)


def _chunked(items: list[Any], size: int) -> list[list[Any]]:
    if size <= 0:
        return [items]
    return [items[i : i + size] for i in range(0, len(items), size)]


def pre_enrich_refs_for_adjudication(refs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Attach regex claims and optional full article body before LLM adjudication."""
    from trade_integrations.dataflows.index_research.news_claim_extraction import enrich_ref_with_claims
    from trade_integrations.dataflows.article_body import enrich_ref_summary_from_url

    out: list[dict[str, Any]] = []
    for ref in refs:
        row = enrich_ref_with_claims(dict(ref))
        row = enrich_ref_summary_from_url(row)
        out.append(row)
    return out


def _rule_check_factor_text(
    factor: str,
    text: str,
    frame: Any,
    publish_day: str,
) -> str | None:
    """Return 'contradicted' when narrative clearly conflicts factor delta."""
    from trade_integrations.dataflows.index_research.news_verification import (
        _FACTOR_CLAIM_RULES,
        _factor_delta,
    )

    rules = _FACTOR_CLAIM_RULES.get(factor, {})
    if not rules:
        return None
    t0, t1, _ = _factor_delta(frame, factor, publish_day)
    if t0 is None or t1 is None:
        return None
    delta = t1 - t0
    sell_hit = any(w in text for w in rules.get("sell_words", ()))
    buy_hit = any(w in text for w in rules.get("buy_words", ()))
    rise_hit = any(w in text for w in rules.get("rise_words", ()))
    fall_hit = any(w in text for w in rules.get("fall_words", ()))

    if factor in {"fii_net_5d", "dii_net_5d"}:
        if sell_hit and delta > 0:
            return "contradicted"
        if buy_hit and delta < 0:
            return "contradicted"
    elif factor in {"oil_brent", "india_vix"}:
        if rise_hit and delta <= 0:
            return "contradicted"
        if fall_hit and delta >= 0:
            return "contradicted"
    return None


def rule_prefilter_adjudication(
    ref: dict[str, Any],
    *,
    frame: Any = None,
) -> AdjudicationVerdict | None:
    """Fast pass/fail; None when LLM adjudication is needed."""
    ref_id = str(ref.get("ref_id") or "")
    text = f"{ref.get('title') or ''} {ref.get('summary') or ''}".strip().lower()
    if not text:
        return AdjudicationVerdict(
            ref_id=ref_id,
            credibility="irrelevant",
            tape_alignment="unverifiable",
            discard=True,
            discard_reason="empty headline",
            source="rule",
        )

    publish_day = publish_day_from_value(str(ref.get("published_at") or ""))
    if frame is not None and publish_day:
        implied = []
        tags = ref.get("tags")
        if isinstance(tags, dict):
            implied = list(tags.get("factors") or [])
        factors = implied or ["oil_brent", "india_vix", "fii_net_5d"]
        for factor in factors[:5]:
            verdict = _rule_check_factor_text(factor, text, frame, publish_day)
            if verdict == "contradicted" and discard_contradicted_enabled():
                return AdjudicationVerdict(
                    ref_id=ref_id,
                    credibility="likely_hoax",
                    tape_alignment="contradicted",
                    discard=True,
                    discard_reason=f"rule contradiction on {factor}",
                    source="rule",
                )
    return None


def _normalize_adjudication_item(item: dict[str, Any], valid_ids: set[str]) -> AdjudicationVerdict | None:
    ref_id = str(item.get("ref_id") or "").strip()
    if ref_id not in valid_ids:
        return None
    tape = str(item.get("tape_alignment") or "unverifiable").strip().lower()
    if tape not in _TAPE_ALIGNMENTS:
        tape = "unverifiable"
    cred = str(item.get("credibility") or "valid").strip().lower()
    if cred not in _CREDIBILITY:
        cred = "valid"
    claims = item.get("claims") if isinstance(item.get("claims"), list) else []
    discard = bool(item.get("discard"))
    discard_reason = str(item.get("discard_reason") or "")
    fingerprint = str(item.get("story_fingerprint") or "")[:120]

    if not discard:
        if cred in {"likely_hoax", "irrelevant"} and discard_hoax_enabled():
            discard = True
            discard_reason = discard_reason or cred
        elif tape == "contradicted" and discard_contradicted_enabled():
            discard = True
            discard_reason = discard_reason or "contradicted by market tape"

    return AdjudicationVerdict(
        ref_id=ref_id,
        claims=[c for c in claims if isinstance(c, dict)][:8],
        tape_alignment=tape,
        credibility=cred,
        discard=discard,
        discard_reason=discard_reason[:300],
        story_fingerprint=fingerprint,
        source="llm",
    )


def _parse_adjudication_response(text: str, valid_ids: set[str]) -> list[AdjudicationVerdict]:
    items = _parse_json_array(text)
    out: list[AdjudicationVerdict] = []
    seen: set[str] = set()
    ordered_ids = sorted(valid_ids)
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            continue
        item = dict(item)
        ref_id = str(item.get("ref_id") or "").strip()
        if ref_id not in valid_ids and index < len(ordered_ids):
            item["ref_id"] = ordered_ids[index]
        verdict = _normalize_adjudication_item(item, valid_ids)
        if verdict and verdict.ref_id not in seen:
            seen.add(verdict.ref_id)
            out.append(verdict)
    return out


def _adjudication_prompt(
    lines: list[str],
    *,
    market_context: dict[str, Any] | None,
    ref_ids: list[str],
) -> str:
    tape = format_market_context_for_prompt(market_context)
    id_list = ", ".join(ref_ids)
    return (
        "You adjudicate Indian market news refs against the market tape. "
        "For each ref: extract factual claims from the text, compare to the tape, "
        "and classify credibility. Do not invent facts. "
        "Use the exact ref_id values listed below — copy each id= value verbatim. "
        f"Required ref_ids: {id_list}. "
        "Output ONLY a JSON array with one object per ref id:\n"
        '[{"ref_id":"<exact id>","claims":[{"type":"oil_price","value":"...","quote":"..."}],'
        '"tape_alignment":"supported|exaggerated|contradicted|unverifiable",'
        '"credibility":"valid|exaggeration|likely_hoax|irrelevant",'
        '"discard":false,"discard_reason":"","story_fingerprint":"YYYY-MM-DD|event_slug"}]\n\n'
        f"{tape}\n\n<refs>\n" + "\n".join(lines) + "\n</refs>"
    )


def _llm_adjudicate_chunk(
    *,
    chunk_ids: list[str],
    id_map: dict[str, dict[str, Any]],
    lines_by_id: dict[str, str],
    market_context: dict[str, Any] | None,
) -> tuple[dict[str, AdjudicationVerdict], bool]:
    llm_ids = set(chunk_ids)
    lines = [lines_by_id[rid] for rid in chunk_ids if rid in lines_by_id]
    prompt = _adjudication_prompt(lines, market_context=market_context, ref_ids=chunk_ids)
    base = adjudication_max_tokens()
    parsed_any = False
    llm_verdicts: dict[str, AdjudicationVerdict] = {}
    last_raw_len = 0
    for max_tokens in _token_attempts(base):
        try:
            raw = call_minimax_json_text(prompt, max_tokens=max_tokens)
            last_raw_len = len(raw or "")
            parsed = _parse_adjudication_response(raw, llm_ids)
            if parsed:
                for verdict in parsed:
                    llm_verdicts[verdict.ref_id] = verdict
                parsed_any = True
                break
        except Exception as exc:
            logger.warning("LLM adjudication attempt failed: %s", exc)
    if not parsed_any:
        logger.warning(
            "LLM adjudication empty parse for %s refs (raw_len=%s); using fallback verdicts",
            len(chunk_ids),
            last_raw_len,
        )
    return llm_verdicts, parsed_any


def _fallback_adjudication(ref: dict[str, Any]) -> AdjudicationVerdict:
    ref_id = str(ref.get("ref_id") or "")
    claims = ref.get("extracted_claims") if isinstance(ref.get("extracted_claims"), list) else []
    day = publish_day_from_value(str(ref.get("published_at") or ""))
    fingerprint = semantic_cluster_key(ref) or f"{day}|unknown"
    return AdjudicationVerdict(
        ref_id=ref_id,
        claims=[dict(c) for c in claims[:8] if isinstance(c, dict)],
        tape_alignment="unverifiable",
        credibility="valid",
        discard=False,
        story_fingerprint=fingerprint[:120],
        source="fallback",
    )


def llm_adjudicate_refs(
    refs: list[dict[str, Any]],
    *,
    market_context: dict[str, Any] | None = None,
    frame: Any = None,
) -> tuple[list[AdjudicationVerdict], dict[str, int]]:
    """Pass A: extract claims and classify credibility vs market tape."""
    stats = {"rule_discarded": 0, "llm_ok": 0, "fallback": 0}
    if not refs:
        return [], stats

    id_map: dict[str, dict[str, Any]] = {}
    lines_by_id: dict[str, str] = {}
    precomputed: dict[str, AdjudicationVerdict] = {}
    for index, ref in enumerate(refs):
        rid = _ref_id(ref, index)
        ref = dict(ref)
        ref["ref_id"] = rid
        id_map[rid] = ref
        rule_hit = rule_prefilter_adjudication(ref, frame=frame)
        if rule_hit is not None:
            precomputed[rid] = rule_hit
            if rule_hit.discard:
                stats["rule_discarded"] += 1
            continue
        lines_by_id[rid] = _ref_summary_line(ref, rid)

    llm_ids = sorted(set(id_map) - set(precomputed))
    llm_verdicts: dict[str, AdjudicationVerdict] = {}
    if llm_ids:
        try:
            require_minimax_for_distillation()
        except Exception:
            for rid in llm_ids:
                llm_verdicts[rid] = _fallback_adjudication(id_map[rid])
                stats["fallback"] += 1
        else:
            chunk_size = max(1, min(adjudication_chunk_size(), len(llm_ids)))
            parsed_total = 0
            for chunk_ids in _chunked(llm_ids, chunk_size):
                chunk_verdicts, parsed_any = _llm_adjudicate_chunk(
                    chunk_ids=chunk_ids,
                    id_map=id_map,
                    lines_by_id=lines_by_id,
                    market_context=market_context,
                )
                llm_verdicts.update(chunk_verdicts)
                if parsed_any:
                    parsed_total += len(chunk_verdicts)
            stats["llm_ok"] = parsed_total
            for rid in llm_ids:
                if rid not in llm_verdicts:
                    llm_verdicts[rid] = _fallback_adjudication(id_map[rid])
                    stats["fallback"] += 1

    ordered: list[AdjudicationVerdict] = []
    for index, ref in enumerate(refs):
        rid = _ref_id(ref, index)
        verdict = precomputed.get(rid) or llm_verdicts.get(rid) or _fallback_adjudication(id_map.get(rid, ref))
        ordered.append(verdict)
    return ordered, stats


def apply_adjudication_discards(
    refs: list[dict[str, Any]],
    verdicts: list[AdjudicationVerdict],
) -> tuple[list[dict[str, Any]], int]:
    """Discard refs marked by adjudication; return kept refs and discard count."""
    from trade_integrations.hub_storage.news_staging_store import mark_ref_discarded

    by_id = {v.ref_id: v for v in verdicts}
    kept: list[dict[str, Any]] = []
    discarded = 0
    for index, ref in enumerate(refs):
        rid = _ref_id(ref, index)
        verdict = by_id.get(rid)
        if verdict is None:
            kept.append(dict(ref))
            continue
        ref = dict(ref)
        ref["adjudication"] = verdict.to_dict()
        if verdict.discard:
            mark_ref_discarded(
                rid,
                reason=verdict.discard_reason or verdict.credibility,
                relevance={"adjudication": verdict.to_dict()},
                restore_payload=ref,
                source_kind="adjudication",
            )
            discarded += 1
            continue
        kept.append(ref)
    return kept, discarded


def mechanical_singleton_groups(
    refs: list[dict[str, Any]],
    *,
    adjudications: list[AdjudicationVerdict] | None = None,
) -> list[dict[str, Any]]:
    """Fallback: one group per ref."""
    adj_by_id = {a.ref_id: a for a in (adjudications or [])}
    groups: list[dict[str, Any]] = []
    for index, ref in enumerate(refs):
        rid = _ref_id(ref, index)
        adj = adj_by_id.get(rid)
        groups.append(
            {
                "group_id": rid,
                "ref_ids": [rid],
                "headline_hint": str(ref.get("title") or "")[:180],
                "why_grouped": "mechanical_singleton",
                "story_fingerprint": (adj.story_fingerprint if adj else "") or "",
                "shared_facts": [],
                "refs": [ref],
                "adjudication_summary": _group_adjudication_summary([adj] if adj else []),
            }
        )
    return groups


def _group_adjudication_summary(verdicts: list[AdjudicationVerdict | None]) -> dict[str, Any]:
    valid = [v for v in verdicts if v is not None]
    if not valid:
        return {}
    creds = [v.credibility for v in valid]
    primary = "exaggeration" if "exaggeration" in creds else creds[0]
    fingerprints = [v.story_fingerprint for v in valid if v.story_fingerprint]
    claims: list[dict[str, Any]] = []
    for v in valid:
        claims.extend(v.claims[:4])
    return {
        "credibility": primary,
        "story_fingerprint": fingerprints[0] if fingerprints else "",
        "shared_facts": [str(c.get("value") or "")[:120] for c in claims[:6] if c.get("value")],
        "ref_count": len(valid),
    }


def mechanical_story_groups(
    refs: list[dict[str, Any]],
    adjudications: list[AdjudicationVerdict],
) -> list[dict[str, Any]]:
    """Group by story fingerprint or semantic cluster key."""
    adj_by_id = {a.ref_id: a for a in adjudications}
    buckets: dict[str, list[dict[str, Any]]] = {}
    order: list[str] = []
    for index, ref in enumerate(refs):
        rid = _ref_id(ref, index)
        adj = adj_by_id.get(rid)
        fp = (adj.story_fingerprint if adj else "") or semantic_cluster_key(ref) or rid
        if fp not in buckets:
            buckets[fp] = []
            order.append(fp)
        buckets[fp].append(dict(ref))

    groups: list[dict[str, Any]] = []
    for fp in order:
        group_refs = buckets[fp]
        ref_ids = [str(r.get("ref_id") or _ref_id(r, 0)) for r in group_refs]
        verdicts = [adj_by_id.get(rid) for rid in ref_ids]
        groups.append(
            {
                "group_id": fp[:80] or ref_ids[0],
                "ref_ids": ref_ids,
                "headline_hint": str(group_refs[0].get("title") or "")[:180],
                "why_grouped": "mechanical_fingerprint",
                "story_fingerprint": fp[:120],
                "shared_facts": _group_adjudication_summary(verdicts).get("shared_facts") or [],
                "refs": group_refs,
                "adjudication_summary": _group_adjudication_summary(verdicts),
            }
        )
    return groups


def _parse_story_groups(text: str, valid_ids: set[str]) -> list[dict[str, Any]]:
    items = _parse_json_array(text)
    groups: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            continue
        ref_ids = [str(r).strip() for r in (item.get("ref_ids") or []) if str(r).strip()]
        ref_ids = [rid for rid in ref_ids if rid in valid_ids and rid not in seen_ids]
        if not ref_ids:
            continue
        for rid in ref_ids:
            seen_ids.add(rid)
        groups.append(
            {
                "group_id": str(item.get("group_id") or ref_ids[0] or f"group:{index}"),
                "ref_ids": ref_ids,
                "headline_hint": str(item.get("headline_hint") or "")[:180],
                "why_grouped": str(item.get("why_grouped") or "")[:300],
                "story_fingerprint": str(item.get("story_fingerprint") or "")[:120],
                "shared_facts": [str(x)[:120] for x in (item.get("shared_facts") or [])[:8]],
            }
        )

    missing = valid_ids - seen_ids
    for rid in sorted(missing):
        groups.append(
            {
                "group_id": rid,
                "ref_ids": [rid],
                "headline_hint": "",
                "why_grouped": "unassigned_singleton",
                "story_fingerprint": "",
                "shared_facts": [],
            }
        )
    return groups


def llm_story_groups(
    refs: list[dict[str, Any]],
    adjudications: list[AdjudicationVerdict],
    *,
    market_context: dict[str, Any] | None = None,
) -> tuple[list[dict[str, Any]], bool]:
    """Pass B: group refs with the same factual story fingerprint."""
    if not refs:
        return [], False
    if len(refs) == 1:
        return mechanical_singleton_groups(refs, adjudications=adjudications), False

    id_map: dict[str, dict[str, Any]] = {}
    adj_by_id = {a.ref_id: a for a in adjudications}
    lines: list[str] = []
    for index, ref in enumerate(refs):
        rid = _ref_id(ref, index)
        ref = dict(ref)
        ref["ref_id"] = rid
        id_map[rid] = ref
        adj = adj_by_id.get(rid)
        fp = f" fingerprint={adj.story_fingerprint}" if adj and adj.story_fingerprint else ""
        lines.append(_ref_summary_line(ref, rid) + fp)

    try:
        require_minimax_for_distillation()
    except Exception:
        return mechanical_story_groups(refs, adjudications), True

    tape = format_market_context_for_prompt(market_context)
    prompt = (
        "You deduplicate Indian market news by factual story, not headline wording. "
        "Group refs that describe the SAME underlying event (same day/cause/facts). "
        "Use story_fingerprint when provided; do not merge unrelated stories sharing keywords. "
        "Every ref id must appear in exactly one group. "
        "Output ONLY a JSON array:\n"
        '[{"group_id":"g1","ref_ids":["<exact id>","<exact id>"],"story_fingerprint":"...",'
        '"shared_facts":["..."],"headline_hint":"...","why_grouped":"..."}]\n\n'
        f"{tape}\n\n<refs>\n" + "\n".join(lines) + "\n</refs>"
    )

    base = story_dedup_max_tokens()
    for max_tokens in _token_attempts(base):
        try:
            raw = call_minimax_json_text(prompt, max_tokens=max_tokens)
            groups = _parse_story_groups(raw, set(id_map))
            if groups:
                for group in groups:
                    group_refs = [id_map[rid] for rid in group["ref_ids"] if rid in id_map]
                    group["refs"] = group_refs
                    group_verdicts = [adj_by_id.get(rid) for rid in group["ref_ids"]]
                    group["adjudication_summary"] = _group_adjudication_summary(group_verdicts)
                    for ref in group_refs:
                        adj = adj_by_id.get(str(ref.get("ref_id") or ""))
                        if adj:
                            ref["adjudication"] = adj.to_dict()
                return groups, False
        except Exception as exc:
            logger.warning("LLM story grouping attempt failed: %s", exc)

    logger.warning("LLM story grouping empty parse; using mechanical fingerprint groups")
    return mechanical_story_groups(refs, adjudications), True


def adjudication_summary_from_refs(refs: list[dict[str, Any]]) -> dict[str, Any]:
    verdicts: list[AdjudicationVerdict] = []
    for ref in refs:
        adj = ref.get("adjudication")
        if not isinstance(adj, dict):
            continue
        verdicts.append(
            AdjudicationVerdict(
                ref_id=str(adj.get("ref_id") or ref.get("ref_id") or ""),
                claims=[c for c in (adj.get("claims") or []) if isinstance(c, dict)],
                tape_alignment=str(adj.get("tape_alignment") or "unverifiable"),
                credibility=str(adj.get("credibility") or "valid"),
                discard=bool(adj.get("discard")),
                discard_reason=str(adj.get("discard_reason") or ""),
                story_fingerprint=str(adj.get("story_fingerprint") or ""),
                source=str(adj.get("source") or "llm"),
            )
        )
    return _group_adjudication_summary(verdicts)


def run_story_pipeline_batch(
    refs: list[dict[str, Any]],
    *,
    market_context: dict[str, Any] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Full Pass A + Pass B pipeline with stats for worker summary."""
    stats: dict[str, Any] = {
        "adjudication_discarded": 0,
        "adjudication_exaggerated": 0,
        "adjudication_valid": 0,
        "adjudication_rule_discarded": 0,
        "adjudication_fallback": 0,
        "story_groups_fallback": False,
        "llm_dedup_groups": 0,
        "mechanical_refs": len(refs),
    }
    if not refs:
        return [], stats

    cap = adjudication_batch_size()
    if len(refs) > cap:
        refs = refs[:cap]

    enriched = pre_enrich_refs_for_adjudication(refs)

    if not llm_adjudication_enabled():
        groups = mechanical_singleton_groups(enriched)
        stats["story_groups_fallback"] = True
        stats["llm_dedup_groups"] = len(groups)
        return groups, stats

    frame = None
    try:
        from trade_integrations.dataflows.index_research.sources.history_loader import (
            load_aligned_factor_history,
        )

        frame, _ = load_aligned_factor_history(days=120)
    except Exception as exc:
        logger.debug("adjudication factor frame unavailable: %s", exc)

    verdicts, adj_stats = llm_adjudicate_refs(enriched, market_context=market_context, frame=frame)
    stats["adjudication_rule_discarded"] = adj_stats.get("rule_discarded", 0)
    stats["adjudication_fallback"] = adj_stats.get("fallback", 0)

    kept, discarded = apply_adjudication_discards(enriched, verdicts)
    stats["adjudication_discarded"] = discarded
    for v in verdicts:
        if v.discard:
            continue
        if v.credibility == "exaggeration":
            stats["adjudication_exaggerated"] += 1
        elif v.credibility == "valid":
            stats["adjudication_valid"] += 1

    if not kept:
        stats["llm_dedup_groups"] = 0
        return [], stats

    kept_verdicts = [v for v in verdicts if not v.discard]
    groups, fallback = llm_story_groups(kept, kept_verdicts, market_context=market_context)
    stats["story_groups_fallback"] = fallback
    stats["llm_dedup_groups"] = len(groups)
    stats["mechanical_refs"] = len(kept)
    return groups, stats
