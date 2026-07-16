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

__all__ = [
    "ResearchKind",
    "ResearchKindContract",
    "ResearchStage",
    "all_contracts",
    "eligible_kinds_for_ticker",
    "get_contract",
    "resolve_kind_for_ticker",
]
