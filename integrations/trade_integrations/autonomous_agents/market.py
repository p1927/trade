"""Execution market helpers for autonomous agents."""

from __future__ import annotations

from typing import Any

from trade_integrations.dataflows.company_research.market import Market, detect_market, india_index_tickers


def symbol_execution_market(symbol: str) -> str:
    """Return ``IN`` or ``US`` for where this symbol is traded."""
    try:
        return detect_market(symbol.strip().upper()).value
    except Exception:
        raw = symbol.strip().upper()
        if raw in india_index_tickers():
            return Market.IN.value
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
