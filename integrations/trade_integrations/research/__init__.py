"""Research orchestration — registry, orchestrator, synthesis."""

from trade_integrations.research.registry import (
    ResearchKind,
    ResearchKindContract,
    ResearchStage,
    all_contracts,
    eligible_kinds_for_ticker,
    get_contract,
    resolve_kind_for_ticker,
)
from trade_integrations.research.orchestrator import ResearchResult, ensure_research_complete

__all__ = [
    "ResearchKind",
    "ResearchKindContract",
    "ResearchStage",
    "ResearchResult",
    "all_contracts",
    "eligible_kinds_for_ticker",
    "ensure_research_complete",
    "get_contract",
    "resolve_kind_for_ticker",
]
