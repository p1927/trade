"""Audit hub events SSOT vs LLM-Wiki raw source exports."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from trade_integrations.dataflows.hub_wiki.bootstrap import migrate_legacy_sources_layout
from trade_integrations.dataflows.hub_wiki.compile import (
    event_slug,
    source_export_is_current,
)
from trade_integrations.dataflows.hub_wiki.config import (
    get_llm_wiki_project_dir,
    legacy_sources_dir,
    llm_wiki_events_dir,
    llm_wiki_news_sources_dir,
)
from trade_integrations.dataflows.hub_wiki.probe import probe_llm_wiki
from trade_integrations.hub_storage.news_events_store import list_events
from trade_integrations.hub_storage.news_migrations import needs_news_migration
from trade_integrations.hub_storage.verified_news_store import verified_records_path


def _legacy_paths_report() -> dict[str, Any]:
    wiki_root = get_llm_wiki_project_dir()
    legacy = legacy_sources_dir()
    legacy_events = llm_wiki_events_dir()
    records = verified_records_path()
    return {
        "legacy_sources_dir": legacy.is_dir(),
        "legacy_sources_files": sum(1 for _ in legacy.rglob("*") if _.is_file()) if legacy.is_dir() else 0,
        "legacy_wiki_events_dir": legacy_events.is_dir(),
        "legacy_wiki_events_files": sum(1 for _ in legacy_events.glob("*.md")) if legacy_events.is_dir() else 0,
        "unmigrated_records_parquet": records.is_file() and needs_news_migration(),
        "wiki_project_exists": wiki_root.is_dir(),
    }


def _dedupe_events_by_id(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    unique: list[dict[str, Any]] = []
    for event in events:
        event_id = str(event.get("event_id") or "").strip()
        if not event_id or event_id in seen:
            continue
        seen.add(event_id)
        unique.append(event)
    return unique


def audit_hub_wiki_sync(
    *,
    ticker: str | None = None,
    run_legacy_migrate: bool = False,
) -> dict[str, Any]:
    """Compare events.parquet coverage against raw/sources/news/ exports."""
    migration: dict[str, Any] = {"skipped": True}
    if run_legacy_migrate:
        migration = migrate_legacy_sources_layout(get_llm_wiki_project_dir())

    sym = (ticker or "").strip().upper() or None
    if sym:
        events = list_events(ticker=sym, limit=50_000, include_rejected=False)
    else:
        from trade_integrations.hub_storage.news_events_store import list_event_tickers

        events = []
        for t in list_event_tickers():
            events.extend(list_events(ticker=t, limit=50_000, include_rejected=False))

    active_events = _dedupe_events_by_id(
        [e for e in events if str(e.get("status") or "active") != "superseded"]
    )
    news_dir = llm_wiki_news_sources_dir()

    missing_export: list[str] = []
    stale_export: list[str] = []
    covered: list[str] = []

    for event in active_events:
        event_id = str(event.get("event_id") or "").strip()
        if not event_id:
            continue
        slug = event_slug(event)
        md_path = news_dir / f"{slug}.md"
        if not md_path.is_file():
            missing_export.append(event_id)
            continue
        if source_export_is_current(event, news_dir=news_dir):
            covered.append(event_id)
        else:
            stale_export.append(event_id)

    event_ids = {str(e.get("event_id") or "") for e in active_events if e.get("event_id")}
    orphan_sources: list[str] = []
    if news_dir.is_dir():
        for md in news_dir.glob("*.md"):
            slug = md.stem
            sidecar_path = news_dir / f"{slug}.json"
            event_id = None
            if sidecar_path.is_file():
                try:
                    payload = json.loads(sidecar_path.read_text(encoding="utf-8"))
                    event_id = str(payload.get("event_id") or "").strip() or None
                except (json.JSONDecodeError, OSError):
                    event_id = None
            if event_id and event_id not in event_ids:
                orphan_sources.append(event_id)

    probe = probe_llm_wiki(force_refresh=True)
    legacy = _legacy_paths_report()

    report: dict[str, Any] = {
        "ticker_filter": sym,
        "events_active": len(active_events),
        "source_md_files": len(list(news_dir.glob("*.md"))) if news_dir.is_dir() else 0,
        "covered": len(covered),
        "missing_export": missing_export,
        "stale_export": stale_export,
        "orphan_source_event_ids": orphan_sources,
        "coverage_pct": round(100.0 * len(covered) / len(active_events), 1) if active_events else 100.0,
        "legacy": legacy,
        "legacy_migrate": migration,
        "llm_wiki_probe": probe,
        "ok": len(missing_export) == 0 and len(stale_export) == 0 and probe.get("ok"),
    }
    return report
