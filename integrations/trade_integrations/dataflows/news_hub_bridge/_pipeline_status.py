"""Aggregate hub news pipeline + LLM-Wiki status for ops and UI."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from trade_integrations.dataflows.hub_wiki import (
    count_project_files,
    embedding_available,
    ensure_llm_wiki_project,
    get_llm_wiki_project_dir,
    health_check,
    llm_wiki_base_url,
    project_path_aligned,
    resolve_project_id,
    search_wiki,
)
from trade_integrations.dataflows.hub_wiki.config import llm_wiki_news_sources_dir
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


def _local_source_file_count(project_dir: Path) -> int:
    news_dir = project_dir / "raw" / "sources" / "news"
    if not news_dir.is_dir():
        return 0
    return sum(1 for p in news_dir.iterdir() if p.is_file() and p.suffix == ".md")


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
    alignment = project_path_aligned(expected_dir=project_dir)
    worker_last = load_worker_last_summary() or {}
    cluster_dedup = worker_last.get("cluster_dedup") if isinstance(worker_last.get("cluster_dedup"), dict) else {}

    llm_wiki_block: dict[str, Any] = {
        "base_url": llm_wiki_base_url(),
        "project_dir": str(project_dir),
        "project_id": resolve_project_id(),
        "health": wiki_health,
        "path_alignment": alignment,
        "embedding_available": embedding_available(),
        "cluster_backend": cluster_dedup.get("backend"),
        "local_source_md_count": _local_source_file_count(project_dir),
        "raw_sources_dir": str(llm_wiki_news_sources_dir()),
    }

    if wiki_health.get("reachable"):
        llm_wiki_block["wiki_page_count"] = count_project_files(root="wiki")
        llm_wiki_block["registered_source_count"] = count_project_files(root="sources")
        probe = search_wiki("NIFTY market news", top_k=3)
        llm_wiki_block["search_probe"] = {
            "ok": bool(probe.get("ok")),
            "mode": probe.get("mode"),
            "hits": len(probe.get("results") or []),
            "token_hits": probe.get("tokenHits"),
            "vector_hits": probe.get("vectorHits"),
        }
        if not alignment.get("aligned"):
            llm_wiki_block["setup_hint"] = (
                "Open LLM Wiki desktop app and point current project at project_dir, "
                "then sync LLM_WIKI_PROJECT_ID from GET /api/v1/projects"
            )

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
        "worker_last": worker_last,
        "migration": migration,
        "llm_wiki": llm_wiki_block,
    }
