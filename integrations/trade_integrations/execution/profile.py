"""Execution profile registry — single routing model for autonomous agents."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Literal

from trade_integrations.autonomous_agents.mandate import (
    MandateConfig,
    mandate_config_from_agent,
    primary_instrument_from_mandate,
)
from trade_integrations.openalgo.market_context import MarketContext

logger = logging.getLogger(__name__)

MarketCode = Literal["IN", "US"]
ModeCode = Literal["paper", "live"]
BackendCode = Literal["openalgo", "alpaca"]
PaperSessionKind = Literal["openalgo_per_agent", "alpaca_account", "none"]
WatchBackend = Literal["nautilus_openalgo", "nautilus_alpaca", "alpaca_quote", "none"]


@dataclass(frozen=True)
class ExecutionProfile:
    market: MarketCode
    mode: ModeCode
    backend: BackendCode
    allowed_instruments: tuple[str, ...]
    paper_session_kind: PaperSessionKind
    prompt_fragment_id: str
    watch_backend: WatchBackend
    uses_openalgo_auto_paper: bool
    uses_nautilus_handoff: bool
    uses_nautilus_watch: bool

    @property
    def is_us(self) -> bool:
        return self.market == "US"

    @property
    def is_paper(self) -> bool:
        return self.mode == "paper"


def _instruments_tuple(mc: MandateConfig) -> tuple[str, ...]:
    items = [str(x).strip().lower() for x in (mc.allowed_instruments or []) if str(x).strip()]
    return tuple(items or ("options",))


def _prompt_fragment_id(
    *,
    market: MarketCode,
    mode: ModeCode,
    instruments: tuple[str, ...],
    mandate_text: str = "",
    symbols: tuple[str, ...] = (),
) -> str:
    has_options = "options" in instruments
    has_equity = "equity" in instruments
    if market == "US":
        if has_options and not has_equity:
            return "us_options_paper" if mode == "paper" else "us_options_live"
        return "us_equity_paper" if mode == "paper" else "us_equity_live"
    if has_equity and not has_options:
        return "in_equity_paper" if mode == "paper" else "in_equity_live"
    mc = MandateConfig(allowed_instruments=list(instruments))
    primary = primary_instrument_from_mandate(
        mc,
        market=market,
        mandate_text=mandate_text,
        symbols=list(symbols) or ["NIFTY"],
    )
    if primary == "equity":
        return "in_equity_paper" if mode == "paper" else "in_equity_live"
    return "in_options_paper" if mode == "paper" else "in_options_live"


def _mode_from_market_context(market_context: MarketContext) -> ModeCode:
    """Derive paper/live mode from authoritative MarketContext."""
    venue = market_context.execution_venue.strip().lower()
    if market_context.market_region == "IN" and venue in ("sandbox", "broker"):
        return "paper" if venue == "sandbox" else "live"
    return "paper" if market_context.analyze_mode else "live"


def _build_execution_profile(
    *,
    market: MarketCode,
    mode: ModeCode,
    agent: dict[str, Any],
) -> ExecutionProfile:
    mc = mandate_config_from_agent(agent)
    instruments = _instruments_tuple(mc)
    sym_tuple = tuple(str(s).upper() for s in (agent.get("symbols") or ["NIFTY"]) if str(s).strip())
    fragment = _prompt_fragment_id(
        market=market,
        mode=mode,
        instruments=instruments,
        mandate_text=str(agent.get("mandate") or ""),
        symbols=sym_tuple or ("NIFTY",),
    )

    if market == "US":
        return ExecutionProfile(
            market="US",
            mode=mode,
            backend="openalgo",
            allowed_instruments=instruments,
            paper_session_kind="openalgo_per_agent" if mode == "paper" else "none",
            prompt_fragment_id=fragment,
            watch_backend="nautilus_openalgo",
            uses_openalgo_auto_paper=True,
            uses_nautilus_handoff=True,
            uses_nautilus_watch=True,
        )

    return ExecutionProfile(
        market="IN",
        mode=mode,
        backend="openalgo",
        allowed_instruments=instruments,
        paper_session_kind="openalgo_per_agent" if mode == "paper" else "none",
        prompt_fragment_id=fragment,
        watch_backend="nautilus_openalgo",
        uses_openalgo_auto_paper=mode == "paper",
        uses_nautilus_handoff=True,
        uses_nautilus_watch=True,
    )


def _market_from_context(*, agent: dict[str, Any], market_context: MarketContext) -> MarketCode:
    """Prefer authoritative market_region from MarketContext over agent record."""
    from trade_integrations.autonomous_agents.market import agent_execution_market

    region = str(market_context.market_region or "").strip().upper()
    if region in ("IN", "US"):
        agent_market = agent_execution_market(agent)
        if agent_market != region:
            logger.warning(
                "resolve_profile_from_context: agent market=%s differs from context region=%s; using context",
                agent_market,
                region,
            )
        return region  # type: ignore[return-value]
    return agent_execution_market(agent)


def resolve_profile_from_context(
    *,
    agent: dict[str, Any],
    market_context: MarketContext,
) -> ExecutionProfile:
    """Derive execution profile from agent mandate plus authoritative MarketContext."""
    market = _market_from_context(agent=agent, market_context=market_context)
    mode = _mode_from_market_context(market_context)
    return _build_execution_profile(market=market, mode=mode, agent=agent)


def resolve_profile(*, agent: dict[str, Any], mode: str | None = None) -> ExecutionProfile:
    """Resolve execution profile; prefer MarketContext when adapter fetch succeeds."""
    from trade_integrations.autonomous_agents.market import agent_execution_market

    constraints = dict(agent.get("constraints") or {})
    fallback_mode = str(mode or constraints.get("mode") or "paper").lower()
    if fallback_mode not in ("paper", "live"):
        fallback_mode = "paper"

    try:
        from trade_integrations.execution.trading_port import adapter_for_agent

        adapter = adapter_for_agent(agent)
        market_context = adapter.market_context()
        return resolve_profile_from_context(agent=agent, market_context=market_context)
    except Exception as exc:
        logger.warning(
            "resolve_profile: MarketContext fetch failed, using agent constraints mode=%s: %s",
            fallback_mode,
            exc,
        )
        return _build_execution_profile(
            market=agent_execution_market(agent),
            mode=fallback_mode,  # type: ignore[arg-type]
            agent=agent,
        )


def profile_for_symbol(symbol: str, *, mode: str = "paper") -> ExecutionProfile:
    """Profile for a symbol before an agent record exists (proposals)."""
    from trade_integrations.autonomous_agents.market import symbol_execution_market

    market = symbol_execution_market(symbol)
    fake_agent: dict[str, Any] = {
        "symbols": [symbol.upper()],
        "execution_market": market,
        "constraints": {"mode": mode},
        "mandate": "",
    }
    return resolve_profile(agent=fake_agent, mode=mode)
