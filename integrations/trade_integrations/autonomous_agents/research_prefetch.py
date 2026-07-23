"""Hub + TradingAgents prefetch helpers for autonomous agent turns."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from trade_integrations.autonomous_agents.store import get_agent
from trade_integrations.execution.routing_context import (
    debate_asset_type_for_agent,
    india_debate_eligible_for_agent,
    research_kinds_for_agent,
)

logger = logging.getLogger(__name__)


async def prefetch_bootstrap_research(agent_id: str) -> None:
    """Warm hub research + debate before the bootstrap Vibe turn."""
    agent = get_agent(agent_id) or {}
    symbols = list(agent.get("symbols") or [])
    if not symbols:
        return
    sym = str(symbols[0]).strip().upper()
    from trade_integrations.bridge.agent_debate import run_agent_debate_locked
    from trade_integrations.research.orchestrator import ensure_research_complete

    kinds = research_kinds_for_agent(agent)

    def hub(*, refresh: bool = False) -> None:
        for kind in kinds:
            ensure_research_complete(sym, kind=kind, refresh=refresh)

    def debate() -> None:
        eligible, _ = india_debate_eligible_for_agent(agent, sym)
        if not eligible:
            return
        from trade_integrations.context.hub import is_agent_debate_cache_fresh

        if is_agent_debate_cache_fresh(sym):
            return
        asset_type = debate_asset_type_for_agent(agent)
        run_agent_debate_locked(sym, asset_type=asset_type, allow_stale_cache=True)

    await asyncio.to_thread(hub, refresh=False)
    try:
        await asyncio.to_thread(debate)
    except Exception as exc:
        logger.warning("bootstrap debate prefetch skipped for %s: %s", sym, exc)


async def prefetch_turn_research(agent_id: str, *, turn_kind: str = "research") -> None:
    """Warm hub research and TradingAgents debate before full-reasoning turns."""
    agent = get_agent(agent_id) or {}
    from trade_integrations.autonomous_agents.mandate_config import is_observe_agent

    if is_observe_agent(agent) or turn_kind == "watch_report":
        symbols = list(agent.get("symbols") or [])
        if not symbols:
            return
        sym = str(symbols[0]).strip().upper()
        from trade_integrations.execution.routing_context import research_kinds_for_agent
        from trade_integrations.research.orchestrator import ensure_research_complete

        kinds = research_kinds_for_agent(agent) or ["index"]

        def hub_only() -> None:
            for kind in kinds:
                ensure_research_complete(sym, kind=kind, refresh=False)

        await asyncio.to_thread(hub_only)
        return

    symbols = list(agent.get("symbols") or [])
    if not symbols:
        return
    sym = str(symbols[0]).strip().upper()
    from trade_integrations.bridge.agent_debate import run_agent_debate_locked
    from trade_integrations.context.hub import is_agent_debate_cache_fresh
    from trade_integrations.research.orchestrator import ensure_research_complete

    kinds = research_kinds_for_agent(agent)
    refresh_hub = turn_kind in {"strategy_revision", "research"}
    eligible, _ = india_debate_eligible_for_agent(agent, sym)
    will_debate = bool(eligible) and (
        turn_kind == "strategy_revision" or not is_agent_debate_cache_fresh(sym)
    )

    def hub(*, refresh: bool = False) -> None:
        for kind in kinds:
            ensure_research_complete(sym, kind=kind, refresh=refresh)

    if will_debate:
        await asyncio.to_thread(hub, refresh=False)
        asset_type = debate_asset_type_for_agent(agent)
        allow_cache = turn_kind != "strategy_revision"
        await asyncio.to_thread(
            run_agent_debate_locked,
            sym,
            asset_type=asset_type,
            allow_stale_cache=allow_cache,
        )
    elif refresh_hub:
        await asyncio.to_thread(hub, refresh=True)
    else:
        await asyncio.to_thread(hub, refresh=False)
