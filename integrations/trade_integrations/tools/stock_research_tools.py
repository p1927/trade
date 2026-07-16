"""Stock trade plan tool for TradingAgents analysts."""

from __future__ import annotations

from typing import Annotated

from langchain_core.tools import tool

from trade_integrations.context.hub import (
    is_stock_cache_fresh,
    is_stock_research_eligible,
    load_stock_research_markdown,
    save_stock_research,
)
from trade_integrations.dataflows.stock_research.aggregator import run_stock_research
from trade_integrations.dataflows.stock_research.format import format_stock_report


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

    if use_cache and is_stock_cache_fresh(ticker):
        cached = load_stock_research_markdown(ticker)
        if cached:
            return cached

    doc = run_stock_research(ticker, lookahead_days=lookahead_days or 14)
    save_stock_research(doc)
    return format_stock_report(doc)


@tool
def get_stock_research(
    ticker: Annotated[str, "Equity ticker symbol, e.g. RELIANCE or TCS"],
    lookahead_days: Annotated[
        int | None,
        "Days ahead for events; omit to use TRADINGAGENTS_RESEARCH_LOOKAHEAD_DAYS",
    ] = None,
) -> str:
    """
    Retrieve a structured stock trade plan for an equity ticker.

    Includes company context from the hub, ranked approaches (event play,
    buy dip, momentum, hold cash), recommended action with entry/target/stop,
    charges, and step-by-step execution payloads for CNC orders.
    """
    return fetch_stock_research_report(ticker, lookahead_days=lookahead_days)
