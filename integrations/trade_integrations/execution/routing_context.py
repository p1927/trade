"""Single routing resolver for autonomous agents (propose → commit → turn → watch)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, TYPE_CHECKING

if TYPE_CHECKING:
    from trade_integrations.execution.profile import ExecutionProfile

from trade_integrations.autonomous_agents.market import agent_execution_market
from trade_integrations.auto_paper.mandate_config import mandate_config_from_agent, primary_instrument_from_mandate

PrimaryInstrument = Literal["options", "equity"]
ResearchAssetType = Literal["options", "stock", "index"]


@dataclass(frozen=True)
class AgentRoutingContext:
    market: Literal["IN", "US"]
    mode: Literal["paper", "live"]
    trade_symbols: tuple[str, ...]
    watch_symbols: tuple[str, ...]
    allowed_instruments: tuple[str, ...]
    primary_instrument: PrimaryInstrument
    prompt_fragment_id: str
    research_asset_type: ResearchAssetType
    uses_strategy_scorer: bool
    uses_options_advisor: bool
    profile: "ExecutionProfile"

    @property
    def is_equity_primary(self) -> bool:
        return self.primary_instrument == "equity"


def _watch_symbols_from_agent(agent: dict[str, Any], trade_symbols: tuple[str, ...]) -> tuple[str, ...]:
    spec = agent.get("watch_spec") or agent.get("watch_rules") or {}
    if not isinstance(spec, dict):
        spec = {}
    mc = agent.get("mandate_config") if isinstance(agent.get("mandate_config"), dict) else {}
    if not spec.get("rules") and isinstance(mc, dict):
        spec = mc.get("watch_spec") or spec
    rules = spec.get("rules") if isinstance(spec, dict) else None
    syms: set[str] = set(trade_symbols)
    if isinstance(rules, list):
        for row in rules:
            if isinstance(row, dict):
                sym = str(row.get("symbol") or "").strip().upper()
                if sym:
                    syms.add(sym)
    return tuple(sorted(syms)) if syms else trade_symbols


def _research_asset_type(
    *,
    market: str,
    primary: PrimaryInstrument,
    trade_symbols: tuple[str, ...],
) -> ResearchAssetType:
    if market == "US":
        return "stock"
    if primary == "equity":
        return "stock"
    sym0 = trade_symbols[0] if trade_symbols else "NIFTY"
    try:
        from trade_integrations.tools.index_research_tools import is_index_research_eligible

        if is_index_research_eligible(sym0):
            return "index"
    except Exception:
        if sym0 in {"NIFTY", "BANKNIFTY", "FINNIFTY", "SENSEX"}:
            return "index"
    return "options"


def resolve_agent_routing(agent: dict[str, Any], *, mode: str | None = None) -> AgentRoutingContext:
    """Resolve unified routing context from a persisted or proposed agent record."""
    from trade_integrations.execution.profile import resolve_profile

    profile = resolve_profile(agent=agent, mode=mode)
    mc = mandate_config_from_agent(agent)
    market = agent_execution_market(agent)
    constraints = dict(agent.get("constraints") or {})
    agent_mode = str(mode or constraints.get("mode") or "paper").lower()
    if agent_mode not in ("paper", "live"):
        agent_mode = "paper"

    trade_symbols = tuple(
        str(s).strip().upper() for s in (agent.get("symbols") or ["NIFTY"]) if str(s).strip()
    ) or ("NIFTY",)
    primary = primary_instrument_from_mandate(
        mc,
        market=market,
        mandate_text=str(agent.get("mandate") or ""),
        symbols=list(trade_symbols),
    )

    if primary == "equity" and profile.prompt_fragment_id.startswith("in_options"):
        fragment = "in_equity_paper" if agent_mode == "paper" else "in_equity_live"
    elif primary == "options" and profile.prompt_fragment_id.startswith("in_equity"):
        fragment = "in_options_paper" if agent_mode == "paper" else "in_options_live"
    else:
        fragment = profile.prompt_fragment_id

    watch_symbols = _watch_symbols_from_agent(agent, trade_symbols)
    research_asset = _research_asset_type(
        market=market,
        primary=primary,
        trade_symbols=trade_symbols,
    )

    return AgentRoutingContext(
        market=market,  # type: ignore[arg-type]
        mode=agent_mode,  # type: ignore[arg-type]
        trade_symbols=trade_symbols,
        watch_symbols=watch_symbols,
        allowed_instruments=profile.allowed_instruments,
        primary_instrument=primary,
        prompt_fragment_id=fragment,
        research_asset_type=research_asset,
        uses_strategy_scorer=(market == "IN" and primary == "options"),
        uses_options_advisor=(market == "IN" and primary == "options"),
        profile=profile,
    )
