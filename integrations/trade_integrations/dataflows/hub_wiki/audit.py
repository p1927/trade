"""Audit hub events SSOT vs LLM-Wiki raw source exports."""

from __future__ import annotations

from typing import Any

from trade_integrations.dataflows.hub_wiki.bootstrap import (
    cleanup_legacy_wiki_artifacts,
    legacy_wiki_layout_report,
)
from trade_integrations.dataflows.hub_wiki.compile import (
    event_slug,
    source_export_is_current,
)
from trade_integrations.dataflows.hub_wiki.config import llm_wiki_news_sources_dir
from trade_integrations.dataflows.hub_wiki.frontmatter import read_frontmatter
from trade_integrations.dataflows.hub_wiki.probe import probe_llm_wiki
from trade_integrations.hub_storage.news_events_store import list_events
from trade_integrations.hub_storage.news_migrations import needs_news_migration
from trade_integrations.hub_storage.verified_news_store import verified_records_path


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
    run_legacy_cleanup: bool = False,
) -> dict[str, Any]:
    """Compare events.parquet coverage against raw/sources/news/ exports."""
    legacy_cleanup: dict[str, Any] = {"skipped": True}
    if run_legacy_cleanup:
        legacy_cleanup = cleanup_legacy_wiki_artifacts(dry_run=False)

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
    expected_slugs = {event_slug(event) for event in active_events}

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
    orphan_md_slugs: list[str] = []
    json_sidecars = 0
    if news_dir.is_dir():
        json_sidecars = len(list(news_dir.glob("*.json")))
        for md in news_dir.glob("*.md"):
            slug = md.stem
            fm = read_frontmatter(md)
            event_id = str(fm.get("event_id") or "").strip() or None
            if event_id and event_id not in event_ids:
                orphan_sources.append(event_id)
            elif slug not in expected_slugs:
                orphan_md_slugs.append(slug)

    probe = probe_llm_wiki(force_refresh=True)
    legacy = legacy_wiki_layout_report()
    records = verified_records_path()
    legacy["unmigrated_records_parquet"] = records.is_file() and needs_news_migration()

    layout_clean = (
        not legacy.get("legacy_sources_dir")
        and int(legacy.get("legacy_sources_files") or 0) == 0
        and not legacy.get("legacy_wiki_events_dir")
        and int(legacy.get("legacy_wiki_events_files") or 0) == 0
    )

    report: dict[str, Any] = {
        "ticker_filter": sym,
        "events_active": len(active_events),
        "source_md_files": len(list(news_dir.glob("*.md"))) if news_dir.is_dir() else 0,
        "json_sidecars_remaining": json_sidecars,
        "covered": len(covered),
        "missing_export": missing_export,
        "stale_export": stale_export,
        "orphan_source_event_ids": orphan_sources,
        "orphan_md_slugs": orphan_md_slugs,
        "coverage_pct": round(100.0 * len(covered) / len(active_events), 1) if active_events else 100.0,
        "legacy": legacy,
        "legacy_layout_clean": layout_clean,
        "legacy_cleanup": legacy_cleanup,
        "llm_wiki_probe": probe,
        "ok": (
            len(missing_export) == 0
            and len(stale_export) == 0
            and not orphan_sources
            and not orphan_md_slugs
            and json_sidecars == 0
            and layout_clean
            and probe.get("ok")
        ),
    }
    return report
