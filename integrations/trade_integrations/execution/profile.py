"""Execution profile registry — single routing model for autonomous agents."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from trade_integrations.autonomous_agents.market import agent_execution_market
from trade_integrations.auto_paper.mandate_config import MandateConfig, mandate_config_from_agent

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


def _prompt_fragment_id(*, market: MarketCode, mode: ModeCode, instruments: tuple[str, ...]) -> str:
    has_options = "options" in instruments
    has_equity = "equity" in instruments
    if market == "US":
        if has_options and not has_equity:
            return "us_options_paper" if mode == "paper" else "us_options_live"
        return "us_equity_paper" if mode == "paper" else "us_equity_live"
    if has_equity and not has_options:
        return "in_equity_paper" if mode == "paper" else "in_equity_live"
    return "in_options_paper" if mode == "paper" else "in_options_live"


def resolve_profile(*, agent: dict[str, Any], mode: str | None = None) -> ExecutionProfile:
    """Resolve execution profile from agent record (stored market + mandate)."""
    market = agent_execution_market(agent)
    constraints = dict(agent.get("constraints") or {})
    agent_mode = str(mode or constraints.get("mode") or "paper").lower()
    if agent_mode not in ("paper", "live"):
        agent_mode = "paper"
    mc = mandate_config_from_agent(agent)
    instruments = _instruments_tuple(mc)
    fragment = _prompt_fragment_id(market=market, mode=agent_mode, instruments=instruments)  # type: ignore[arg-type]

    if market == "US":
        return ExecutionProfile(
            market="US",
            mode=agent_mode,  # type: ignore[arg-type]
            backend="alpaca",
            allowed_instruments=instruments,
            paper_session_kind="alpaca_account" if agent_mode == "paper" else "none",
            prompt_fragment_id=fragment,
            watch_backend="nautilus_alpaca",
            uses_openalgo_auto_paper=False,
            uses_nautilus_handoff=False,
            uses_nautilus_watch=True,
        )

    return ExecutionProfile(
        market="IN",
        mode=agent_mode,  # type: ignore[arg-type]
        backend="openalgo",
        allowed_instruments=instruments,
        paper_session_kind="openalgo_per_agent" if agent_mode == "paper" else "none",
        prompt_fragment_id=fragment,
        watch_backend="nautilus_openalgo",
        uses_openalgo_auto_paper=agent_mode == "paper",
        uses_nautilus_handoff=True,
        uses_nautilus_watch=True,
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
