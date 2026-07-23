"""Compile distilled hub events into LLM-Wiki source exports (raw/sources/news/)."""

from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from trade_integrations.dataflows.hub_wiki.bootstrap import ensure_llm_wiki_project
from trade_integrations.dataflows.hub_wiki.client import trigger_sources_rescan
from trade_integrations.dataflows.hub_wiki.config import llm_wiki_news_sources_dir
from trade_integrations.dataflows.hub_wiki.frontmatter import frontmatter_field, read_frontmatter


def wiki_compile_enabled() -> bool:
    return os.getenv("HUB_NEWS_WIKI_COMPILE", "1").strip().lower() in {"1", "true", "yes", "on"}


def wiki_backfill_enabled() -> bool:
    return os.getenv("HUB_NEWS_WIKI_BACKFILL", "0").strip().lower() in {"1", "true", "yes", "on"}


def event_content_fingerprint(event: dict[str, Any]) -> str:
    """Stable hash for skip-if-unchanged wiki exports."""
    def _norm(text: Any) -> str:
        return str(text or "").strip().replace("\n", " ")

    title = _norm(event.get("title"))
    body = _norm(event.get("content") or event.get("content_summary"))
    version = int(event.get("processing_version") or 1)
    return f"{title}|{body}|v{version}"


def _slug(text: str, *, max_len: int = 64) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", (text or "").strip().lower()).strip("-")
    return (slug or "event")[:max_len]


def _event_id_slug_suffix(event_id: str) -> str:
    raw = (event_id or "").strip()
    if not raw:
        return ""
    if raw.startswith("url:"):
        return _slug(raw[-12:], max_len=16)
    compact = re.sub(r"[^a-z0-9]+", "", raw.lower())
    tail = compact[-12:] or compact[:12]
    return _slug(tail, max_len=16)


def event_slug(event: dict[str, Any]) -> str:
    event_id = str(event.get("event_id") or "").strip()
    title = str(event.get("title") or "event")
    suffix = _event_id_slug_suffix(event_id)
    if suffix:
        title_slug = _slug(title, max_len=48)
        return _slug(f"{title_slug}-{suffix}", max_len=64)
    return _slug(title, max_len=64)


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


def render_event_source(event: dict[str, Any], *, source_rel_path: str) -> str:
    """Markdown for raw/sources/news/{slug}.md — LLM Wiki ingest input."""
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
        f"title: {_yaml_value(title)}",
        f"sources: [{_yaml_value(source_rel_path)}]",
        f"event_id: {_yaml_value(event_id)}",
        f"parent_event_id: {_yaml_value(parent)}",
        f"ticker: {_yaml_value(ticker)}",
        f"provenance: {_yaml_value(meta.get('provenance') or 'live')}",
        f"market_impact_status: {_yaml_value(meta.get('market_impact_status') or 'unverified')}",
        f"distillation_mode: {_yaml_value(meta.get('distillation_mode') or meta.get('distilled_by') or 'unknown')}",
        f"compiled_at: {_now_iso()}",
        f"processing_version: {int(event.get('processing_version') or meta.get('processing_version') or 1)}",
        f"source_count: {int(meta.get('ref_count') or len(refs) or 1)}",
        f"linked_factors: {_yaml_value(factors)}",
        f"publish_day: {_yaml_value(event.get('publish_day') or '')}",
        f"content_fingerprint: {_yaml_value(event_content_fingerprint(event))}",
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


def compile_event_to_wiki(
    event: dict[str, Any],
    *,
    rescan: bool = False,
) -> dict[str, Any]:
    """Export one distilled event to raw/sources/news/ for LLM Wiki ingest."""
    ensure_llm_wiki_project()
    event_id = str(event.get("event_id") or "").strip()
    if not event_id:
        return {"ok": False, "error": "missing event_id"}

    slug = event_slug(event)
    news_dir = llm_wiki_news_sources_dir()
    news_dir.mkdir(parents=True, exist_ok=True)

    source_rel = f"news/{slug}.md"
    md_path = news_dir / f"{slug}.md"

    md_path.write_text(render_event_source(event, source_rel_path=source_rel), encoding="utf-8")
    legacy_json = news_dir / f"{slug}.json"
    if legacy_json.is_file():
        legacy_json.unlink()

    out: dict[str, Any] = {
        "ok": True,
        "event_id": event_id,
        "source_md_path": str(md_path),
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
    """Export event source + trigger LLM-Wiki rescan."""
    return compile_event_by_id(event_id, rescan=True)


def batch_rescan_if_enabled(*, enabled: bool = True) -> dict[str, Any]:
    """Single rescan after a worker/rollup batch (avoids per-event queue spam)."""
    if not enabled or not wiki_compile_enabled():
        return {"ok": True, "skipped": True}
    return trigger_sources_rescan()


def source_export_is_current(event: dict[str, Any], *, news_dir: Path | None = None) -> bool:
    root = news_dir or llm_wiki_news_sources_dir()
    slug = event_slug(event)
    md_path = root / f"{slug}.md"
    if not md_path.is_file():
        return False
    return frontmatter_field(md_path, "content_fingerprint") == event_content_fingerprint(event)


def compile_all_events_to_wiki(
    *,
    ticker: str | None = None,
    dry_run: bool = False,
    force: bool = False,
    rescan: bool = True,
    limit: int = 50_000,
) -> dict[str, Any]:
    """Export all active distilled events to raw/sources/news/."""
    from trade_integrations.hub_storage.news_events_store import list_event_tickers, list_events

    if not wiki_compile_enabled():
        return {"ok": False, "skipped": True, "reason": "HUB_NEWS_WIKI_COMPILE disabled"}

    ensure_llm_wiki_project()
    sym = (ticker or "").strip().upper() or None
    events: list[dict[str, Any]] = []
    if sym:
        events = list_events(ticker=sym, limit=limit, include_rejected=False)
    else:
        for t in list_event_tickers():
            events.extend(list_events(ticker=t, limit=limit, include_rejected=False))

    active = [e for e in events if str(e.get("status") or "active") != "superseded"]
    seen_ids: set[str] = set()
    unique_active: list[dict[str, Any]] = []
    for event in active:
        event_id = str(event.get("event_id") or "").strip()
        if not event_id or event_id in seen_ids:
            continue
        seen_ids.add(event_id)
        unique_active.append(event)
    active = unique_active
    compiled = 0
    skipped = 0
    errors: list[str] = []

    for event in active:
        event_id = str(event.get("event_id") or "").strip()
        if not event_id:
            continue
        if not force and source_export_is_current(event):
            skipped += 1
            continue
        if dry_run:
            compiled += 1
            continue
        result = compile_event_to_wiki(event, rescan=False)
        if result.get("ok"):
            compiled += 1
        else:
            errors.append(f"{event_id}: {result.get('error')}")

    out: dict[str, Any] = {
        "ok": not errors,
        "dry_run": dry_run,
        "events_active": len(active),
        "compiled": compiled,
        "skipped_current": skipped,
        "errors": errors,
    }
    if not dry_run and compiled > 0 and rescan:
        out["rescan"] = batch_rescan_if_enabled()
    return out


def cleanup_orphan_wiki_source_exports(
    *,
    ticker: str | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Remove raw/sources/news files that no longer map to active SSOT events."""
    from trade_integrations.hub_storage.news_events_store import list_event_tickers, list_events

    sym = (ticker or "").strip().upper() or None
    events: list[dict[str, Any]] = []
    if sym:
        events = list_events(ticker=sym, limit=50_000, include_rejected=False)
    else:
        for t in list_event_tickers():
            events.extend(list_events(ticker=t, limit=50_000, include_rejected=False))

    seen_ids: set[str] = set()
    active: list[dict[str, Any]] = []
    for event in events:
        if str(event.get("status") or "active") == "superseded":
            continue
        event_id = str(event.get("event_id") or "").strip()
        if not event_id or event_id in seen_ids:
            continue
        seen_ids.add(event_id)
        active.append(event)

    expected_slugs = {event_slug(event) for event in active}
    expected_ids = seen_ids
    news_dir = llm_wiki_news_sources_dir()
    removed: list[str] = []

    if news_dir.is_dir():
        for md_path in news_dir.glob("*.md"):
            slug = md_path.stem
            fm = read_frontmatter(md_path)
            event_id = str(fm.get("event_id") or "").strip()
            orphan = slug not in expected_slugs or (event_id and event_id not in expected_ids)
            if not orphan:
                continue
            if not dry_run:
                md_path.unlink()
            removed.append(str(md_path))

        for json_path in news_dir.glob("*.json"):
            if not dry_run:
                json_path.unlink()
            removed.append(str(json_path))

    return {
        "ok": True,
        "dry_run": dry_run,
        "removed": removed,
        "removed_count": len(removed),
        "expected_slugs": len(expected_slugs),
    }


def remove_event_wiki_files(event: dict[str, Any], *, rescan: bool = False) -> dict[str, Any]:
    """Remove exported source files for a discarded or archived event."""
    event_id = str(event.get("event_id") or "").strip()
    if not event_id:
        return {"ok": False, "error": "missing event_id"}

    slug = event_slug(event)
    removed: list[str] = []
    md_path = llm_wiki_news_sources_dir() / f"{slug}.md"
    json_path = llm_wiki_news_sources_dir() / f"{slug}.json"
    for path in (md_path, json_path):
        if path.is_file():
            path.unlink()
            removed.append(str(path))

    out: dict[str, Any] = {"ok": True, "event_id": event_id, "removed": removed}
    if rescan and removed:
        out["rescan"] = trigger_sources_rescan()
    return out


def purge_json_wiki_sidecars(*, dry_run: bool = False) -> dict[str, Any]:
    """Remove legacy JSON sidecars from raw/sources/news/ and raw/sources/research/."""
    from trade_integrations.dataflows.hub_wiki.config import (
        llm_wiki_news_sources_dir,
        llm_wiki_research_sources_dir,
    )

    removed: list[str] = []
    for root in (llm_wiki_news_sources_dir(), llm_wiki_research_sources_dir()):
        if not root.is_dir():
            continue
        for json_path in root.glob("*.json"):
            if not dry_run:
                json_path.unlink()
            removed.append(str(json_path))
    return {"ok": True, "dry_run": dry_run, "removed": removed, "removed_count": len(removed)}
