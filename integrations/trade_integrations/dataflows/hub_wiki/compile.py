"""Compile distilled hub events into LLM-Wiki markdown (derived layer)."""

from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from typing import Any

from trade_integrations.dataflows.hub_wiki.bootstrap import ensure_llm_wiki_project
from trade_integrations.dataflows.hub_wiki.client import trigger_sources_rescan
from trade_integrations.dataflows.hub_wiki.config import (
    llm_wiki_events_dir,
    llm_wiki_sources_dir,
)


def wiki_compile_enabled() -> bool:
    return os.getenv("HUB_NEWS_WIKI_COMPILE", "1").strip().lower() in {"1", "true", "yes", "on"}


def _slug(text: str, *, max_len: int = 64) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", (text or "").strip().lower()).strip("-")
    return (slug or "event")[:max_len]


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _yaml_value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (list, dict)):
        return json.dumps(value, ensure_ascii=False)
    return str(value).replace("\n", " ")


def render_event_page(event: dict[str, Any]) -> str:
    """Markdown for wiki/events/{slug}.md from a distilled event dict."""
    structured = event.get("structured_summary") if isinstance(event.get("structured_summary"), dict) else {}
    meta = structured.get("event_meta") if isinstance(structured.get("event_meta"), dict) else {}
    consensus = meta.get("consensus") if isinstance(meta.get("consensus"), dict) else {}
    refs = meta.get("references") or event.get("references") or []
    timeline = meta.get("timeline") or event.get("timeline") or []

    event_id = str(event.get("event_id") or meta.get("event_id") or "")
    title = str(event.get("title") or "Market event")
    ticker = str(event.get("ticker") or "NIFTY")
    parent = meta.get("parent_event_id")
    factors = list(consensus.get("factors") or consensus.get("primary_factors") or [])[:8]

    fm_lines = [
        "---",
        "type: event",
        f"event_id: {_yaml_value(event_id)}",
        f"parent_event_id: {_yaml_value(parent)}",
        f"title: {_yaml_value(title)}",
        f"ticker: {_yaml_value(ticker)}",
        f"provenance: {_yaml_value(meta.get('provenance') or 'live')}",
        f"market_impact_status: {_yaml_value(meta.get('market_impact_status') or 'unverified')}",
        f"distillation_mode: {_yaml_value(meta.get('distillation_mode') or meta.get('distilled_by') or 'unknown')}",
        f"compiled_at: {_now_iso()}",
        f"processing_version: {int(event.get('processing_version') or meta.get('processing_version') or 1)}",
        f"source_count: {int(meta.get('ref_count') or len(refs) or 1)}",
        f"linked_factors: {_yaml_value(factors)}",
        f"publish_day: {_yaml_value(event.get('publish_day') or '')}",
        "---",
        "",
        f"# {title}",
        "",
        str(event.get("content") or "").strip(),
        "",
        "## Timeline",
        "",
    ]
    if timeline:
        for entry in timeline[-15:]:
            if not isinstance(entry, dict):
                continue
            at = entry.get("at") or ""
            kind = entry.get("kind") or "update"
            summary = entry.get("summary") or entry.get("raw_title") or ""
            fm_lines.append(f"- **{at}** ({kind}) — {summary}")
    else:
        fm_lines.append("- (no timeline entries)")

    fm_lines.extend(["", "## Sources", ""])
    for ref in refs[:20]:
        if not isinstance(ref, dict):
            continue
        pub = ref.get("publisher") or ref.get("vendor") or "source"
        raw_title = ref.get("raw_title") or ref.get("title") or ""
        url = ref.get("url") or ""
        claims = ref.get("extracted_claims") or []
        fm_lines.append(f"- **{pub}**: {raw_title}")
        if url:
            fm_lines.append(f"  - {url}")
        for claim in claims[:5]:
            if isinstance(claim, dict):
                fm_lines.append(
                    f"  - claim [{claim.get('status', 'claimed')}]: "
                    f"{claim.get('kind')} = {claim.get('value')}"
                )

    conflicts = consensus.get("conflicts") or []
    if conflicts:
        fm_lines.extend(["", "## Conflicts", ""])
        for row in conflicts[:10]:
            fm_lines.append(f"- {row}")

    fm_lines.append("")
    return "\n".join(fm_lines)


def render_source_export(event: dict[str, Any]) -> str:
    """JSON export under sources/news/ for immutable ref audit."""
    structured = event.get("structured_summary") if isinstance(event.get("structured_summary"), dict) else {}
    meta = structured.get("event_meta") if isinstance(structured.get("event_meta"), dict) else {}
    payload = {
        "event_id": event.get("event_id"),
        "title": event.get("title"),
        "ticker": event.get("ticker"),
        "publish_day": event.get("publish_day"),
        "compiled_at": _now_iso(),
        "references": meta.get("references") or [],
        "timeline": meta.get("timeline") or [],
        "consensus": meta.get("consensus") or {},
    }
    return json.dumps(payload, indent=2, ensure_ascii=False) + "\n"


def compile_event_to_wiki(
    event: dict[str, Any],
    *,
    rescan: bool = False,
) -> dict[str, Any]:
    """Write one distilled event to LLM-Wiki project tree."""
    ensure_llm_wiki_project()
    event_id = str(event.get("event_id") or "").strip()
    title = str(event.get("title") or "event")
    if not event_id:
        return {"ok": False, "error": "missing event_id"}

    slug = _slug(title)
    if event_id.startswith("url:"):
        slug = _slug(f"{slug}-{event_id[-12:]}")
    else:
        slug = _slug(f"{slug}-{event_id[:8]}")

    event_path = llm_wiki_events_dir() / f"{slug}.md"
    source_path = llm_wiki_sources_dir() / "news" / f"{slug}.json"
    source_path.parent.mkdir(parents=True, exist_ok=True)

    event_path.write_text(render_event_page(event), encoding="utf-8")
    source_path.write_text(render_source_export(event), encoding="utf-8")

    out: dict[str, Any] = {
        "ok": True,
        "event_id": event_id,
        "wiki_path": str(event_path),
        "source_path": str(source_path),
        "slug": slug,
    }
    if rescan:
        out["rescan"] = trigger_sources_rescan()
    return out


def compile_event_by_id(event_id: str, *, rescan: bool = False) -> dict[str, Any]:
    from trade_integrations.hub_storage.news_events_store import get_event

    event = get_event(event_id)
    if not event:
        return {"ok": False, "error": f"event not found: {event_id}"}
    return compile_event_to_wiki(event, rescan=rescan)


def compile_and_rescan_event(event_id: str) -> dict[str, Any]:
    """Compile event page + trigger LLM-Wiki source rescan."""
    return compile_event_by_id(event_id, rescan=True)


def remove_event_wiki_files(event: dict[str, Any]) -> dict[str, Any]:
    """Remove compiled wiki markdown/json for a discarded or archived event."""
    event_id = str(event.get("event_id") or "").strip()
    title = str(event.get("title") or "event")
    if not event_id:
        return {"ok": False, "error": "missing event_id"}

    slug = _slug(title)
    if event_id.startswith("url:"):
        slug = _slug(f"{slug}-{event_id[-12:]}")
    else:
        slug = _slug(f"{slug}-{event_id[:8]}")

    removed: list[str] = []
    event_path = llm_wiki_events_dir() / f"{slug}.md"
    source_path = llm_wiki_sources_dir() / "news" / f"{slug}.json"
    for path in (event_path, source_path):
        if path.is_file():
            path.unlink()
            removed.append(str(path))
    return {"ok": True, "event_id": event_id, "removed": removed}
