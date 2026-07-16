"""Options research tool for TradingAgents analysts."""

from __future__ import annotations

from typing import Annotated

from trade_integrations.dataflows.options_research.format import format_options_report
from trade_integrations.dataflows.options_research.market import is_options_research_eligible
from trade_integrations.research.orchestrator import ensure_research_complete
from trade_integrations.research.registry import ResearchKind


def fetch_options_research_report(
    ticker: str,
    *,
    expiry_date: str | None = None,
    lookahead_days: int | None = None,
    use_cache: bool = True,
) -> str:
    """Run or load the options trade plan for one underlying."""
    if not is_options_research_eligible(ticker):
        return f"Options research is not available for {ticker!r}."

    result = ensure_research_complete(
        ticker,
        kind=ResearchKind.OPTIONS,
        refresh=not use_cache,
        horizon_days=lookahead_days or 14,
        expiry_date=expiry_date,
        require_debate=False,
    )
    if result.error and result.doc is None:
        return f"Options research failed for {ticker}: {result.error}"
    if result.doc is None:
        return f"No options research available for {ticker}."
    return format_options_report(result.doc)


def get_options_research(
    ticker: Annotated[str, "Options underlying, e.g. NIFTY or RELIANCE"],
    expiry_date: Annotated[
        str | None,
        "Option expiry DDMMMYY; omit to use nearest expiry from chain",
    ] = None,
    lookahead_days: Annotated[
        int | None,
        "Days ahead for events; omit to use TRADINGAGENTS_OPTIONS_LOOKAHEAD_DAYS",
    ] = None,
) -> str:
    """
    Retrieve a structured options trade plan for an index or F&O stock.

    Includes chain snapshot, event context, ranked strategies, recommended
    legs with payoff/charges, and step-by-step MCP execution payloads.
    Cached in the trade-stack hub for reuse across agents.
    """
    return fetch_options_research_report(
        ticker,
        expiry_date=expiry_date,
        lookahead_days=lookahead_days,
    )


try:
    from langchain_core.tools import tool as _lc_tool

    get_options_research = _lc_tool(get_options_research)
except ImportError:
    # OpenAlgo MCP loads fetch_options_research_report without langchain installed.
    pass
