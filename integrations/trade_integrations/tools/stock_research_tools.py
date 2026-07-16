"""Stock trade plan tool for TradingAgents analysts."""

from __future__ import annotations

from typing import Annotated

from trade_integrations.context.hub import is_stock_research_eligible
from trade_integrations.dataflows.stock_research.format import format_stock_report
from trade_integrations.research.orchestrator import ensure_research_complete
from trade_integrations.research.registry import ResearchKind


def fetch_stock_research_report(
    ticker: str,
    *,
    lookahead_days: int | None = None,
    use_cache: bool = True,
) -> str:
    """Run or load the equity trade plan for one ticker."""
    if not is_stock_research_eligible(ticker):
        return (
            f"Stock trade plan is not available for {ticker!r} "
            "(indices and non-stock instruments are excluded)."
        )

    result = ensure_research_complete(
        ticker,
        kind=ResearchKind.STOCK,
        refresh=not use_cache,
        horizon_days=lookahead_days or 14,
        require_debate=False,
    )
    if result.error and result.doc is None:
        return f"Stock research failed for {ticker}: {result.error}"
    if result.doc is None:
        return f"No stock research available for {ticker}."
    return format_stock_report(result.doc)


def get_stock_research(
    ticker: Annotated[str, "Equity ticker symbol, e.g. RELIANCE or TCS"],
    lookahead_days: Annotated[
        int | None,
        "Days ahead for events; omit to use TRADINGAGENTS_RESEARCH_LOOKAHEAD_DAYS",
    ] = None,
) -> str:
    """
    Retrieve a structured stock trade plan for an equity ticker.

    Includes directional view, events, ranked setups, and implementation steps.
    Cached in the trade-stack hub for reuse across agents.
    """
    return fetch_stock_research_report(ticker, lookahead_days=lookahead_days)


try:
    from langchain_core.tools import tool as _lc_tool

    get_stock_research = _lc_tool(get_stock_research)
except ImportError:
    pass
