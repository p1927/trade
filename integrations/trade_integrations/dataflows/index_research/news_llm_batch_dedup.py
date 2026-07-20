"""LLM pass 1: batch dedup staging refs into story groups before distillation.

Deprecated: implementation moved to ``news_llm_story_pipeline``. This module
keeps public names for backward compatibility.
"""

from __future__ import annotations

from typing import Any

from trade_integrations.dataflows.index_research.news_llm_story_pipeline import (
    mechanical_singleton_groups as _mechanical_singleton_groups,
    run_story_pipeline_batch,
)

__all__ = [
    "llm_batch_dedup_groups",
    "mechanical_singleton_groups",
]


def mechanical_singleton_groups(refs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return _mechanical_singleton_groups(refs)


def llm_batch_dedup_groups(
    refs: list[dict[str, Any]],
    *,
    market_context: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Group refs describing the same market story (story pipeline Pass B)."""
    groups, _stats = run_story_pipeline_batch(refs, market_context=market_context)
    return groups
