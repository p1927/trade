"""Aggregate hub news pipeline + LLM-Wiki status for ops and UI."""

from __future__ import annotations

from typing import Any

from trade_integrations.dataflows.hub_wiki import (
    ensure_llm_wiki_project,
    get_llm_wiki_project_dir,
    health_check,
    llm_wiki_base_url,
    resolve_project_id,
)
from trade_integrations.dataflows.index_research.news_entity_worker import load_worker_last_summary
from trade_integrations.hub_storage.news_migrations import load_migration_state, needs_news_migration
from trade_integrations.hub_storage.news_staging_store import (
    discarded_count,
    is_entity_pipeline_enabled,
    is_legacy_ingest_enabled,
    pipeline_pause_status,
    staging_queue_detail,
)
from trade_integrations.hub_storage.news_events_store import count_events
from trade_integrations.dataflows.index_research.news_relevance import relevance_gate_enabled


def hub_news_pipeline_status(*, ticker: str = "NIFTY") -> dict[str, Any]:
    """Unified pipeline health: staging, SSOT, migration, LLM-Wiki."""
    sym = ticker.strip().upper()
    pause = pipeline_pause_status(ticker=sym)
    pending = pause.get("pending") or staging_queue_detail(ticker=sym)

    wiki_health: dict[str, Any] = {"reachable": False}
    try:
        wiki_health = health_check()
        wiki_health["reachable"] = bool(wiki_health.get("ok"))
    except Exception as exc:
        wiki_health = {"ok": False, "reachable": False, "error": str(exc)[:200]}

    project_dir = ensure_llm_wiki_project()

    migration = {
        "needed": needs_news_migration(ticker=sym),
        "state": load_migration_state(),
    }

    return {
        "ticker": sym,
        "ssot": "events.parquet",
        "entity_pipeline_enabled": is_entity_pipeline_enabled(),
        "legacy_ingest_enabled": is_legacy_ingest_enabled(),
        "pipeline_paused": bool(pause.get("pipeline_paused")),
        "pause_reason": str(pause.get("pause_reason") or ""),
        "minimax_configured": bool(pause.get("minimax_configured")),
        "staging": dict(pending),
        "discarded_count": discarded_count(ticker=sym),
        "relevance_gate_enabled": relevance_gate_enabled(),
        "distilled_event_count": count_events(ticker=sym),
        "worker_last": load_worker_last_summary(),
        "migration": migration,
        "llm_wiki": {
            "base_url": llm_wiki_base_url(),
            "project_dir": str(project_dir),
            "project_id": resolve_project_id(),
            "health": wiki_health,
        },
    }
