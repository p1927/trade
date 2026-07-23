"""Agent-centric execution status (replaces autonomous_agents get_status for autonomous agents)."""

from __future__ import annotations

import logging
from dataclasses import asdict, dataclass
from typing import Any

from trade_integrations.autonomous_agents.market_hours import is_trading_session_open
from trade_integrations.autonomous_agents.mandate import mandate_config_from_agent
from trade_integrations.autonomous_agents.reconcile import reconcile_paper_state
from trade_integrations.monitor.execution_ledger import list_open_entries_live
from trade_integrations.openalgo.market_context import MarketContext

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class OpenAlgoAuthority:
    """Single OpenAlgo round-trip: MarketContext authority + optional funds."""

    market_context: MarketContext | None
    execution_context: dict[str, Any] | None
    funds: dict[str, Any] | None


def load_openalgo_authority(*, agent: dict[str, Any] | None = None) -> OpenAlgoAuthority:
    """One OpenAlgo client session: MarketContext API (+ funds when context succeeds)."""
    try:
        from trade_integrations.execution.openalgo_client import OpenAlgoClient

        client = OpenAlgoClient()
        market_context = client.get_market_context()
        profile_id = str((agent or {}).get("connector_profile_id") or "").strip().lower() or None
        execution_context = market_context.to_execution_context_summary(profile_id=profile_id)
        funds: dict[str, Any] | None = None
        try:
            funds = client.get_funds()
        except Exception:
            logger.debug("OpenAlgo funds unavailable", exc_info=True)
        return OpenAlgoAuthority(
            market_context=market_context,
            execution_context=execution_context,
            funds=funds,
        )
    except RuntimeError:
        return OpenAlgoAuthority(market_context=None, execution_context=None, funds=None)
    except Exception:
        logger.debug("OpenAlgo MarketContext unavailable", exc_info=True)
        return OpenAlgoAuthority(market_context=None, execution_context=None, funds=None)


def build_execution_context_summary(*, agent: dict[str, Any] | None = None) -> dict[str, Any] | None:
    """OpenAlgo MarketContext summary; optional profile_id audit from agent record."""
    return load_openalgo_authority(agent=agent).execution_context


def get_agent_execution_status(
    *,
    agent_id: str | None = None,
    agent: dict[str, Any] | None = None,
    authority: OpenAlgoAuthority | None = None,
) -> dict[str, Any]:
    """Open positions, reconcile, and OpenAlgo execution context — keyed by agent when provided."""
    if agent is None and agent_id:
        from trade_integrations.autonomous_agents.store import get_agent

        agent = get_agent(agent_id)

    if authority is None:
        authority = load_openalgo_authority(agent=agent)

    open_entries = list_open_entries_live()
    position_summary = [
        {
            "widget_id": entry.get("widget_id"),
            "underlying": entry.get("underlying"),
            "recommended_name": entry.get("recommended_name"),
            "execution_mode": entry.get("execution_mode"),
            "net_max_loss": entry.get("net_max_loss"),
        }
        for entry in open_entries
    ]

    execution_context = authority.execution_context
    analyze_mode = execution_context.get("analyze_mode") if execution_context else None

    mc_dict: dict[str, Any] = {}
    fallback_market = "IN"
    if agent:
        mc = mandate_config_from_agent(agent)
        mc_dict = mc.to_dict()
        fallback_market = str(agent.get("execution_market") or "IN").upper()

    market_region = (
        str(execution_context.get("market_region") or "").upper()
        if execution_context
        else fallback_market
    )
    if market_region not in ("IN", "US"):
        market_region = fallback_market

    reconcile = reconcile_paper_state()
    lifecycle = (agent or {}).get("lifecycle")

    return {
        "session": {
            "enabled": str((agent or {}).get("status") or "") == "running",
            "autonomous_agent_id": agent_id or (agent or {}).get("id"),
            "primary_ticker": ((agent or {}).get("symbols") or ["NIFTY"])[0],
            "lifecycle": lifecycle,
            "last_decision": (agent or {}).get("last_decision"),
            "last_agent_turn_at": (agent or {}).get("last_agent_turn_at"),
            "halted": str((agent or {}).get("status") or "") == "paused",
        },
        "mandate_config": mc_dict,
        "scheduler_health": None,
        "market_open": is_trading_session_open(market=market_region),
        "open_positions": len(open_entries),
        "positions": position_summary,
        "funds": authority.funds,
        "analyze_mode": analyze_mode,
        "execution_context": execution_context,
        "reconcile": asdict(reconcile),
        "lifecycle": lifecycle,
    }
