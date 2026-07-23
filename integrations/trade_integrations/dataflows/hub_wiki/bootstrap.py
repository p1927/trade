"""Ensure LLM-Wiki project directories exist under the hub root."""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

from trade_integrations.dataflows.hub_wiki.config import (
    get_llm_wiki_project_dir,
    llm_wiki_concepts_dir,
    llm_wiki_entities_dir,
    llm_wiki_news_sources_dir,
    llm_wiki_queries_dir,
    llm_wiki_raw_assets_dir,
    llm_wiki_research_sources_dir,
    llm_wiki_sources_dir,
    llm_wiki_synthesis_dir,
    llm_wiki_wiki_dir,
    llm_wiki_wiki_sources_dir,
)

_TEMPLATES = Path(__file__).resolve().parent / "templates"

_LEGACY_SOURCES_DIRNAME = "sources"
_LEGACY_WIKI_EVENTS_SUBDIR = "events"


def _copy_if_missing(src: Path, dest: Path) -> None:
    if dest.is_file() or not src.is_file():
        return
    dest.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")


def _legacy_sources_dir() -> Path:
    return get_llm_wiki_project_dir() / _LEGACY_SOURCES_DIRNAME


def _legacy_wiki_events_dir() -> Path:
    return llm_wiki_wiki_dir() / _LEGACY_WIKI_EVENTS_SUBDIR


def cleanup_legacy_wiki_artifacts(*, dry_run: bool = False) -> dict[str, Any]:
    """Remove pre-cutover llm-wiki paths superseded by ``raw/sources/news/`` exports."""
    removed_files = 0
    removed_dirs: list[str] = []

    legacy_src = _legacy_sources_dir()
    if legacy_src.is_dir():
        for item in legacy_src.rglob("*"):
            if item.is_file():
                removed_files += 1
                if not dry_run:
                    item.unlink()
        if not dry_run:
            shutil.rmtree(legacy_src, ignore_errors=True)
        removed_dirs.append(str(legacy_src))

    legacy_events = _legacy_wiki_events_dir()
    if legacy_events.is_dir():
        for md in legacy_events.glob("*.md"):
            removed_files += 1
            if not dry_run:
                md.unlink()
        if not dry_run:
            try:
                legacy_events.rmdir()
            except OSError:
                shutil.rmtree(legacy_events, ignore_errors=True)
        removed_dirs.append(str(legacy_events))

    return {
        "ok": True,
        "dry_run": dry_run,
        "removed_files": removed_files,
        "removed_dirs": removed_dirs,
    }


def legacy_wiki_layout_report() -> dict[str, Any]:
    """Report remaining deprecated on-disk layout (should be empty post-cutover)."""
    legacy_src = _legacy_sources_dir()
    legacy_events = _legacy_wiki_events_dir()
    return {
        "legacy_sources_dir": legacy_src.is_dir(),
        "legacy_sources_files": sum(1 for _ in legacy_src.rglob("*") if _.is_file()) if legacy_src.is_dir() else 0,
        "legacy_wiki_events_dir": legacy_events.is_dir(),
        "legacy_wiki_events_files": sum(1 for _ in legacy_events.glob("*.md")) if legacy_events.is_dir() else 0,
        "wiki_project_exists": get_llm_wiki_project_dir().is_dir(),
    }


def ensure_llm_wiki_project() -> Path:
    """Create llm-wiki/ tree if missing; seed purpose/schema/wiki stubs."""
    root = get_llm_wiki_project_dir()
    for path in (
        root,
        llm_wiki_sources_dir(),
        llm_wiki_sources_dir() / "inbox",
        llm_wiki_news_sources_dir(),
        llm_wiki_research_sources_dir(),
        llm_wiki_raw_assets_dir(),
        llm_wiki_wiki_dir(),
        llm_wiki_entities_dir(),
        llm_wiki_concepts_dir(),
        llm_wiki_wiki_sources_dir(),
        llm_wiki_queries_dir(),
        llm_wiki_synthesis_dir(),
    ):
        path.mkdir(parents=True, exist_ok=True)

    _copy_if_missing(_TEMPLATES / "purpose.md", root / "purpose.md")
    _copy_if_missing(_TEMPLATES / "schema.md", root / "schema.md")
    _copy_if_missing(_TEMPLATES / "wiki_index.md", llm_wiki_wiki_dir() / "index.md")
    _copy_if_missing(_TEMPLATES / "wiki_log.md", llm_wiki_wiki_dir() / "log.md")
    _copy_if_missing(_TEMPLATES / "wiki_overview.md", llm_wiki_wiki_dir() / "overview.md")

    return root
