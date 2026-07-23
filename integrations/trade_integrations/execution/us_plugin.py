"""US agent execution routes through OpenAlgo Alpaca broker plugin."""

from __future__ import annotations

from typing import Any


def openalgo_us_via_plugin() -> bool:
    """US autonomous execution always uses OpenAlgo (Alpaca plugin)."""
    return True


def us_execution_via_openalgo(agent: dict[str, Any] | None = None) -> bool:
    """True when this agent's US execution goes through OpenAlgo."""
    if agent is None:
        return True
    try:
        from trade_integrations.autonomous_agents.market import agent_execution_market

        return agent_execution_market(agent) == "US"
    except Exception:
        return False
