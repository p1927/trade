"""Ensure LLM-Wiki project directories exist under the hub root."""

from __future__ import annotations

import shutil
from pathlib import Path

from trade_integrations.dataflows.hub_wiki.config import (
    get_llm_wiki_project_dir,
    legacy_sources_dir,
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


def _copy_if_missing(src: Path, dest: Path) -> None:
    if dest.is_file() or not src.is_file():
        return
    dest.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")


def migrate_legacy_sources_layout(root: Path) -> dict[str, int]:
    """Move ``sources/*`` → ``raw/sources/*`` from pre-fix Trade layout."""
    legacy = legacy_sources_dir()
    if not legacy.is_dir():
        return {"moved": 0}

    moved = 0
    for sub in ("inbox", "news"):
        src_dir = legacy / sub
        if not src_dir.is_dir():
            continue
        dest_dir = llm_wiki_sources_dir() / sub
        dest_dir.mkdir(parents=True, exist_ok=True)
        for item in src_dir.iterdir():
            target = dest_dir / item.name
            if target.exists():
                continue
            shutil.move(str(item), str(target))
            moved += 1

    # Migrate legacy wiki/events markdown into raw/sources/news for ingest
    legacy_events = llm_wiki_wiki_dir() / "events"
    if legacy_events.is_dir():
        dest_news = llm_wiki_news_sources_dir()
        dest_news.mkdir(parents=True, exist_ok=True)
        for md in legacy_events.glob("*.md"):
            target = dest_news / md.name
            if target.exists():
                continue
            shutil.copy2(md, target)
            moved += 1

    try:
        if legacy.is_dir() and not any(legacy.iterdir()):
            legacy.rmdir()
        elif legacy.is_dir():
            for sub in legacy.iterdir():
                if sub.is_dir() and not any(sub.iterdir()):
                    sub.rmdir()
            if not any(legacy.iterdir()):
                legacy.rmdir()
    except OSError:
        pass

    return {"moved": moved}


def ensure_llm_wiki_project() -> Path:
    """Create llm-wiki/ tree if missing; seed purpose/schema/wiki stubs; migrate legacy paths."""
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

    migrate_legacy_sources_layout(root)
    return root
