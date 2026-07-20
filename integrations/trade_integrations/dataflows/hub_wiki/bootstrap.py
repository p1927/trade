"""Ensure LLM-Wiki project directories exist under the hub root."""

from __future__ import annotations

from pathlib import Path

from trade_integrations.dataflows.hub_wiki.config import (
    get_llm_wiki_project_dir,
    llm_wiki_entities_dir,
    llm_wiki_events_dir,
    llm_wiki_sources_dir,
    llm_wiki_wiki_dir,
)

_TEMPLATES = Path(__file__).resolve().parent / "templates"


def ensure_llm_wiki_project() -> Path:
    """Create llm-wiki/ tree if missing; seed index and schema from templates."""
    root = get_llm_wiki_project_dir()
    for path in (
        root,
        llm_wiki_sources_dir(),
        llm_wiki_sources_dir() / "inbox",
        llm_wiki_sources_dir() / "news",
        llm_wiki_wiki_dir(),
        llm_wiki_events_dir(),
        llm_wiki_entities_dir(),
        llm_wiki_wiki_dir() / "themes",
    ):
        path.mkdir(parents=True, exist_ok=True)

    _copy_if_missing(_TEMPLATES / "schema.md", root / "schema.md")
    _copy_if_missing(_TEMPLATES / "wiki_index.md", llm_wiki_wiki_dir() / "index.md")
    _copy_if_missing(_TEMPLATES / "wiki_log.md", llm_wiki_wiki_dir() / "log.md")
    _copy_if_missing(_TEMPLATES / "wiki_overview.md", llm_wiki_wiki_dir() / "overview.md")
    return root


def _copy_if_missing(src: Path, dest: Path) -> None:
    if dest.is_file() or not src.is_file():
        return
    dest.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
