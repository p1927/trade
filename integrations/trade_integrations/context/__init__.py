"""Shared context hub for research artifacts consumed by agents and Vibe."""

from .hub import (
    get_hub_dir,
    is_company_research_eligible,
    load_company_research_markdown,
    prefetch_company_research,
    save_company_research,
)

__all__ = [
    "get_hub_dir",
    "is_company_research_eligible",
    "load_company_research_markdown",
    "prefetch_company_research",
    "save_company_research",
]
