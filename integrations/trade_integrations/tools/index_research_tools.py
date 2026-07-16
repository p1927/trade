"""Index research tool for TradingAgents analysts."""

from __future__ import annotations

from typing import Annotated

from trade_integrations.dataflows.company_research.india_symbols import india_index_tickers
from trade_integrations.dataflows.index_research.format import format_index_report
from trade_integrations.research.orchestrator import ensure_research_complete
from trade_integrations.research.registry import ResearchKind


def is_index_research_eligible(ticker: str) -> bool:
    """Return True when the index research pipeline applies."""
    raw = ticker.strip().upper().replace(".NS", "").replace(".BO", "")
    return raw in india_index_tickers()


def fetch_index_research_report(
    ticker: str,
    *,
    horizon_days: int | None = None,
    use_cache: bool = True,
    refresh_constituents: bool = False,
) -> str:
    """Run or load the index research report for one index ticker."""
    sym = ticker.strip().upper().replace(".NS", "").replace(".BO", "")
    if not is_index_research_eligible(sym):
        return (
            f"Index research is not available for {ticker!r}. "
            "Supported indices include NIFTY, BANKNIFTY, and other NSE index symbols."
        )

    result = ensure_research_complete(
        sym,
        kind=ResearchKind.INDEX,
        refresh=not use_cache,
        horizon_days=horizon_days or 14,
        refresh_constituents=refresh_constituents,
        require_debate=False,
    )
    if result.error and result.doc is None:
        return f"Index research failed for {sym}: {result.error}"
    if result.doc is None:
        return f"No index research available for {sym}."
    return format_index_report(result.doc)


def get_index_research(
    ticker: Annotated[str, "Index ticker, e.g. NIFTY or BANKNIFTY"],
    horizon_days: Annotated[
        int | None,
        "Prediction horizon in days; omit to use INDEX_RESEARCH_HORIZON_DAYS (default 14)",
    ] = None,
) -> str:
    """
    Retrieve structured Nifty/index research with prediction, attribution, and scenarios.

    Includes spot, horizon-aware range forecast, constituent contributions, macro factors,
    regime classification, scenario table, and model accuracy metrics from the hub cache.
    """
    return fetch_index_research_report(ticker, horizon_days=horizon_days)


try:
    from langchain_core.tools import tool as _lc_tool

    get_index_research = _lc_tool(get_index_research)
except ImportError:
    # OpenAlgo MCP loads fetch_index_research_report without langchain installed.
    pass
