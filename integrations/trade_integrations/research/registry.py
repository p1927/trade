"""Unified research contract registry for options, stock, and index trade plans."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Callable, Literal

ProducerKind = Literal["batch", "debate", "synthesis", "live_quote"]


class ResearchKind(str, Enum):
    OPTIONS = "options"
    STOCK = "stock"
    INDEX = "index"


@dataclass(frozen=True)
class ResearchStage:
    id: str
    producer: ProducerKind
    required: bool
    hub_subdir: str | None = None
    parallel_group: int = 0


@dataclass(frozen=True)
class ResearchKindContract:
    kind: ResearchKind
    hub_subdir: str
    widget_intent: str
    stages: tuple[ResearchStage, ...]
    required_widget_fields: tuple[str, ...]
    eligibility: Callable[[str], bool]


def _options_eligible(ticker: str) -> bool:
    from trade_integrations.dataflows.options_research.market import is_options_research_eligible

    return is_options_research_eligible(ticker)


def _stock_eligible(ticker: str) -> bool:
    from trade_integrations.context.hub import is_stock_research_eligible

    return is_stock_research_eligible(ticker)


def _index_eligible(ticker: str) -> bool:
    from trade_integrations.tools.index_research_tools import is_index_research_eligible

    return is_index_research_eligible(ticker)


_OPTIONS_CONTRACT = ResearchKindContract(
    kind=ResearchKind.OPTIONS,
    hub_subdir="options_research",
    widget_intent="options_strategy",
    stages=(
        ResearchStage("company_research", "batch", True, "company_research", 0),
        ResearchStage("options_research", "batch", True, "options_research", 0),
        ResearchStage("agent_debate", "debate", False, "agent_debate", 1),
        ResearchStage("debate_synthesis", "synthesis", False, None, 2),
        ResearchStage("live_quote", "live_quote", True, None, 0),
    ),
    required_widget_fields=(
        "recommended.legs",
        "payoff.samples",
        "charges.net_debit_credit",
        "charges.round_trip_charges",
    ),
    eligibility=_options_eligible,
)

_STOCK_CONTRACT = ResearchKindContract(
    kind=ResearchKind.STOCK,
    hub_subdir="stock_research",
    widget_intent="stock_trade",
    stages=(
        ResearchStage("company_research", "batch", True, "company_research", 0),
        ResearchStage("agent_debate", "debate", True, "agent_debate", 1),
        ResearchStage("stock_quant_predict", "batch", True, None, 1),
        ResearchStage("debate_synthesis", "synthesis", True, None, 2),
        ResearchStage("stock_research", "batch", True, "stock_research", 2),
        ResearchStage("live_quote", "live_quote", True, None, 0),
    ),
    required_widget_fields=(
        "prediction.range.low",
        "prediction.range.high",
        "prediction.provenance",
        "recommended.max_profit",
        "recommended.max_loss",
        "charges.round_trip_charges",
    ),
    eligibility=_stock_eligible,
)

_INDEX_CONTRACT = ResearchKindContract(
    kind=ResearchKind.INDEX,
    hub_subdir="index_research",
    widget_intent="index_outlook",
    stages=(
        ResearchStage("index_research", "batch", True, "index_research", 0),
        ResearchStage("agent_debate", "debate", False, "agent_debate", 1),
        ResearchStage("debate_synthesis", "synthesis", False, None, 2),
        ResearchStage("live_quote", "live_quote", True, None, 0),
    ),
    required_widget_fields=(
        "prediction.expected_return_pct",
        "prediction.range.low",
        "prediction.range.high",
        "scenarios",
    ),
    eligibility=_index_eligible,
)

_CONTRACTS: dict[ResearchKind, ResearchKindContract] = {
    ResearchKind.OPTIONS: _OPTIONS_CONTRACT,
    ResearchKind.STOCK: _STOCK_CONTRACT,
    ResearchKind.INDEX: _INDEX_CONTRACT,
}


def get_contract(kind: ResearchKind | str) -> ResearchKindContract:
    if isinstance(kind, str):
        kind = ResearchKind(kind)
    return _CONTRACTS[kind]


def all_contracts() -> tuple[ResearchKindContract, ...]:
    return tuple(_CONTRACTS.values())


def eligible_kinds_for_ticker(ticker: str) -> tuple[ResearchKind, ...]:
    """Return all research kinds that apply to ticker (may be multiple, e.g. NIFTY)."""
    out: list[ResearchKind] = []
    for contract in _CONTRACTS.values():
        if contract.eligibility(ticker):
            out.append(contract.kind)
    return tuple(out)


def resolve_kind_for_ticker(
    ticker: str,
    *,
    prefer: ResearchKind | str | None = None,
) -> ResearchKind | None:
    """Pick a single research kind for ticker; prefer explicit hint when eligible."""
    eligible = eligible_kinds_for_ticker(ticker)
    if not eligible:
        return None
    if prefer is not None:
        pref = ResearchKind(prefer) if isinstance(prefer, str) else prefer
        if pref in eligible:
            return pref
    if ResearchKind.INDEX in eligible and len(eligible) == 1:
        return ResearchKind.INDEX
    if ResearchKind.STOCK in eligible and ResearchKind.OPTIONS not in eligible:
        return ResearchKind.STOCK
    if ResearchKind.OPTIONS in eligible:
        return ResearchKind.OPTIONS
    return eligible[0]
