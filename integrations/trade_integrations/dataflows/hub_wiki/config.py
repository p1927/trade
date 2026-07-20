"""LLM-Wiki project paths and API configuration.

Storage layout (under hub root, default ``reports/hub/``):

    llm-wiki/
      purpose.md
      schema.md
      raw/
        sources/          # immutable exports from hub events (LLM Wiki auto-watch)
        sources/inbox/    # manual drop zone
        sources/news/     # Trade distilled event markdown + JSON audit
        assets/
      wiki/               # LLM Wiki-generated pages after ingest (do not write events/ directly)

Parquet/json SSOT stays in ``_data/`` — wiki is a derived, regeneratable layer.
Point the LLM-Wiki desktop app at ``get_llm_wiki_project_dir()`` after migration.
"""

from __future__ import annotations

import os
from pathlib import Path

from trade_integrations.context.hub import get_hub_dir

_LLM_WIKI_DIRNAME = "llm-wiki"
_BASE_URL_ENV = "LLM_WIKI_BASE_URL"
_PROJECT_ID_ENV = "LLM_WIKI_PROJECT_ID"
_API_TOKEN_ENV = "LLM_WIKI_API_TOKEN"
_DEFAULT_BASE_URL = "http://127.0.0.1:19828"


def llm_wiki_base_url() -> str:
    return os.getenv(_BASE_URL_ENV, _DEFAULT_BASE_URL).strip().rstrip("/")


def llm_wiki_project_id() -> str:
    return os.getenv(_PROJECT_ID_ENV, "").strip()


def llm_wiki_api_token() -> str:
    return os.getenv(_API_TOKEN_ENV, "").strip()


def get_llm_wiki_project_dir() -> Path:
    """Root directory for the LLM-Wiki Obsidian project (co-located with hub)."""
    return get_hub_dir() / _LLM_WIKI_DIRNAME


def llm_wiki_raw_dir() -> Path:
    return get_llm_wiki_project_dir() / "raw"


def llm_wiki_sources_dir() -> Path:
    """Immutable source tree watched by LLM Wiki (``raw/sources/``)."""
    return llm_wiki_raw_dir() / "sources"


def llm_wiki_raw_assets_dir() -> Path:
    return llm_wiki_raw_dir() / "assets"


def llm_wiki_news_sources_dir() -> Path:
    return llm_wiki_sources_dir() / "news"


def llm_wiki_wiki_dir() -> Path:
    return get_llm_wiki_project_dir() / "wiki"


def llm_wiki_events_dir() -> Path:
    """Legacy path — prefer ``raw/sources/news/`` for new exports."""
    return llm_wiki_wiki_dir() / "events"


def llm_wiki_entities_dir() -> Path:
    return llm_wiki_wiki_dir() / "entities"


def legacy_sources_dir() -> Path:
    """Pre-migration ``sources/`` tree (removed after one-shot migrate)."""
    return get_llm_wiki_project_dir() / "sources"
