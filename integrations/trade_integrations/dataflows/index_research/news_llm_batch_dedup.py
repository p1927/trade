"""LLM pass 1: batch dedup staging refs into story groups before distillation."""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from trade_integrations.dataflows.index_research.news_distillation import (
    _distill_max_tokens,
    call_minimax_text,
    strip_minimax_thinking,
)
from trade_integrations.dataflows.index_research.news_market_context import (
    format_market_context_for_prompt,
)
from trade_integrations.hub_storage.news_staging_store import require_minimax_for_distillation

logger = logging.getLogger(__name__)

_JSON_ARRAY = re.compile(r"\[\s*\{.*\}\s*\]", re.DOTALL)


def _ref_id(ref: dict[str, Any], index: int) -> str:
    rid = str(ref.get("ref_id") or "").strip()
    if rid:
        return rid
    return f"idx:{index}"


def _ref_summary_line(ref: dict[str, Any], ref_id: str) -> str:
    title = str(ref.get("title") or "")[:200]
    summary = str(ref.get("summary") or "")[:400]
    url = str(ref.get("url") or "")[:200]
    pub = str(ref.get("published_at") or "")[:32]
    publisher = ""
    sources = ref.get("sources")
    if isinstance(sources, list) and sources:
        publisher = str((sources[0] or {}).get("publisher") or "")
    return (
        f"id={ref_id} | pub={pub} | publisher={publisher} | "
        f"title={title} | summary={summary} | url={url}"
    )


def mechanical_singleton_groups(refs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Fallback: one group per ref."""
    groups: list[dict[str, Any]] = []
    for index, ref in enumerate(refs):
        rid = _ref_id(ref, index)
        groups.append(
            {
                "group_id": rid,
                "ref_ids": [rid],
                "headline_hint": str(ref.get("title") or "")[:180],
                "why_grouped": "mechanical_singleton",
            }
        )
    return groups


def _parse_batch_groups(text: str, valid_ids: set[str]) -> list[dict[str, Any]]:
    text = strip_minimax_thinking(text)
    match = _JSON_ARRAY.search(text)
    if not match:
        return []
    try:
        payload = json.loads(match.group(0))
    except json.JSONDecodeError:
        return []
    if not isinstance(payload, list):
        return []

    groups: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for index, item in enumerate(payload):
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
            }
        )
    return groups


def llm_batch_dedup_groups(
    refs: list[dict[str, Any]],
    *,
    market_context: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Group refs describing the same market story (MiniMax pass 1)."""
    if not refs:
        return []
    if len(refs) == 1:
        return mechanical_singleton_groups(refs)

    id_map: dict[str, dict[str, Any]] = {}
    lines: list[str] = []
    for index, ref in enumerate(refs):
        rid = _ref_id(ref, index)
        ref = dict(ref)
        ref["ref_id"] = rid
        id_map[rid] = ref
        lines.append(_ref_summary_line(ref, rid))

    try:
        require_minimax_for_distillation()
    except Exception:
        return mechanical_singleton_groups(list(id_map.values()))

    tape = format_market_context_for_prompt(market_context)
    prompt = (
        "You deduplicate financial news refs for Indian markets. "
        "Group refs that describe the SAME underlying market story (same event/day/cause). "
        "Do not merge unrelated stories that merely share a keyword. "
        "Every ref id must appear in exactly one group. "
        "Output ONLY a JSON array: "
        '[{"group_id":"g1","ref_ids":["id1","id2"],"headline_hint":"...","why_grouped":"..."}]\n\n'
        f"{tape}\n\n<refs>\n" + "\n".join(lines) + "\n</refs>"
    )

    base_tokens = max(600, _distill_max_tokens())
    for max_tokens in (base_tokens, max(base_tokens + 400, 1200)):
        try:
            raw = call_minimax_text(prompt, max_tokens=max_tokens)
            groups = _parse_batch_groups(raw, set(id_map))
            if groups:
                for group in groups:
                    group["refs"] = [id_map[rid] for rid in group["ref_ids"] if rid in id_map]
                return groups
        except Exception as exc:
            logger.warning("LLM batch dedup attempt failed: %s", exc)

    logger.warning("LLM batch dedup empty parse; using mechanical singleton groups")
    return mechanical_singleton_groups(list(id_map.values()))
