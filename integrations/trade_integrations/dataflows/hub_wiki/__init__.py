"""Hub LLM-Wiki integration — derived markdown layer on top of events SSOT."""

from trade_integrations.dataflows.hub_wiki.bootstrap import ensure_llm_wiki_project
from trade_integrations.dataflows.hub_wiki.client import (
    health_check,
    list_projects,
    resolve_project_id,
    search_wiki,
    trigger_sources_rescan,
)
from trade_integrations.dataflows.hub_wiki.compile import (
    compile_and_rescan_event,
    compile_event_by_id,
    compile_event_to_wiki,
    wiki_compile_enabled,
)
from trade_integrations.dataflows.hub_wiki.config import (
    get_llm_wiki_project_dir,
    llm_wiki_base_url,
    llm_wiki_project_id,
)
from trade_integrations.dataflows.hub_wiki.embeddings import (
    embedding_available,
    fetch_embedding,
    load_embedding_config,
)

__all__ = [
    "compile_and_rescan_event",
    "compile_event_by_id",
    "compile_event_to_wiki",
    "embedding_available",
    "ensure_llm_wiki_project",
    "fetch_embedding",
    "get_llm_wiki_project_dir",
    "health_check",
    "list_projects",
    "load_embedding_config",
    "llm_wiki_base_url",
    "llm_wiki_project_id",
    "resolve_project_id",
    "search_wiki",
    "trigger_sources_rescan",
    "wiki_compile_enabled",
]
