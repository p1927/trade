"""Company research tool for TradingAgents analysts."""

from __future__ import annotations

from typing import Annotated

from trade_integrations.context.hub import (
    is_cache_fresh,
    is_company_research_eligible,
    load_company_research_markdown,
    save_company_research,
)
from trade_integrations.dataflows.company_research.aggregator import run_company_research
from trade_integrations.dataflows.company_research.format import format_research_report


def fetch_company_research_report(
    ticker: str,
    *,
    lookahead_days: int | None = None,
    use_cache: bool = True,
) -> str:
    """Run or load the company research dossier for one equity ticker."""
    if not is_company_research_eligible(ticker):
        return (
            f"Company research is not available for {ticker!r} "
            "(indices and non-stock instruments are excluded)."
        )

    if use_cache and is_cache_fresh(ticker):
        cached = load_company_research_markdown(ticker)
        if cached:
            return cached

    doc = run_company_research(ticker, lookahead_days=lookahead_days)
    save_company_research(doc)
    return format_research_report(doc)


def get_company_research(
    ticker: Annotated[str, "Equity ticker symbol, e.g. RELIANCE or AAPL"],
    lookahead_days: Annotated[
        int | None,
        "Days ahead for corporate events; omit to use TRADINGAGENTS_RESEARCH_LOOKAHEAD_DAYS",
    ] = None,
) -> str:
    """
    Retrieve a structured company research dossier for an equity ticker.

    Includes company identity, live price when OpenAlgo is configured,
    upcoming calendar events (results, board meetings), and pipeline stage
    health. Data is cached in the trade-stack hub for reuse across agents.
    """
    return fetch_company_research_report(ticker, lookahead_days=lookahead_days)


try:
    from langchain_core.tools import tool as _lc_tool

    get_company_research = _lc_tool(get_company_research)
except ImportError:
    pass
