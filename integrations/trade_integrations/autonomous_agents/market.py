"""Execution market helpers for autonomous agents."""

from __future__ import annotations

from typing import Any

from trade_integrations.autonomous_agents.market_resolve import resolve_execution_market
from trade_integrations.dataflows.company_research.market import Market, detect_market, india_index_tickers


def symbol_execution_market(
    symbol: str,
    *,
    user_text: str = "",
    market_hint: str | None = None,
) -> str:
    """Return ``IN`` or ``US`` for where this symbol is traded."""
    try:
        return resolve_execution_market(
            symbol.strip().upper(),
            user_text=user_text,
            market_hint=market_hint,
        ).market
    except Exception:
        raw = symbol.strip().upper()
        if raw in india_index_tickers():
            return Market.IN.value
        try:
            return detect_market(raw).value
        except Exception:
            return Market.US.value


def agent_execution_market(agent: dict[str, Any]) -> str:
    """Resolve agent execution backend market from stored fields or symbols."""
    stored = str(agent.get("execution_market") or "").upper()
    if stored in {"IN", "US"}:
        return stored
    symbols = list(agent.get("symbols") or [])
    if symbols:
        return symbol_execution_market(str(symbols[0]))
    return Market.IN.value


def is_us_agent(agent: dict[str, Any]) -> bool:
    return agent_execution_market(agent) == Market.US.value
