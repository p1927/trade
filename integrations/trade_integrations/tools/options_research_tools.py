"""Options research tool for TradingAgents analysts."""

from __future__ import annotations

from typing import Annotated

from trade_integrations.context.hub import (
    is_options_cache_fresh,
    load_options_research_json,
    load_options_research_markdown,
    save_options_research,
)
from trade_integrations.dataflows.options_research.market import is_options_research_eligible
from trade_integrations.dataflows.options_research.aggregator import run_options_research
from trade_integrations.dataflows.options_research.format import format_options_report


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

    if use_cache and is_options_cache_fresh(ticker):
        doc = load_options_research_json(ticker)
        if doc is not None and _options_doc_is_usable(doc):
            cached = load_options_research_markdown(ticker)
            if cached:
                return cached
        use_cache = False

    doc = run_options_research(
        ticker,
        expiry_date=expiry_date,
        lookahead_days=lookahead_days,
    )
    save_options_research(doc)
    return format_options_report(doc)


def _options_doc_is_usable(doc) -> bool:
    """True when cached hub doc has ranked strategies (chain succeeded)."""
    rec = doc.recommended or {}
    if rec.get("name") and doc.ranked_strategies:
        return True
    for stage in doc.stages or []:
        if getattr(stage, "stage", None) == "chain" and getattr(stage, "status", None) == "error":
            return False
    return bool(doc.ranked_strategies)


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
