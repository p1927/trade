"""Trade-stack runtime hooks for the TradingAgents submodule."""

from __future__ import annotations

import os

_APPLIED = False


def apply() -> None:
    """Register OpenAlgo, SearXNG, RSS, news-aggregator, and company research."""
    global _APPLIED
    if _APPLIED:
        return
    _APPLIED = True

    _patch_default_config()
    _patch_vendor_routing()
    _patch_sentiment_analyst()
    _patch_news_analyst()
    _patch_trading_graph()


def _patch_default_config() -> None:
    import tradingagents.default_config as default_config

    cfg = default_config.DEFAULT_CONFIG
    cfg["sentiment_rss_feeds"] = [
        {
            "label": "Google News",
            "url": (
                "https://news.google.com/rss/search?q={search_term}+stock+when:7d"
                "&hl=en-US&gl=US&ceid=US:en"
            ),
        },
        {
            "label": "Yahoo Finance Headlines",
            "url": (
                "https://feeds.finance.yahoo.com/rss/2.0/headline"
                "?s={ticker}&region=US&lang=en-US"
            ),
        },
    ]
    cfg["news_aggregator_sources"] = os.getenv(
        "TRADINGAGENTS_NEWS_AGGREGATOR_SOURCES",
        "searxng,yfinance,alpha_vantage",
    )
    alpaca_on = bool(os.getenv("ALPACA_API_KEY") and (
        os.getenv("ALPACA_API_SECRET") or os.getenv("ALPACA_SECRET_KEY")
    ))
    openalgo_on = bool(os.getenv("OPENALGO_API_KEY"))
    stock_vendors = []
    if openalgo_on:
        stock_vendors.append("openalgo")
    if alpaca_on:
        stock_vendors.append("alpaca")
    stock_vendors.append("yfinance")
    stock_chain = os.getenv("TRADINGAGENTS_CORE_STOCK_DATA_VENDOR") or ",".join(stock_vendors)
    indicator_chain = os.getenv("TRADINGAGENTS_TECHNICAL_INDICATORS_DATA_VENDOR") or stock_chain
    cfg["data_vendors"] = {
        "core_stock_apis": stock_chain,
        "technical_indicators": indicator_chain,
        "fundamental_data": os.getenv(
            "TRADINGAGENTS_FUNDAMENTAL_DATA_VENDOR",
            "yfinance",
        ),
        "news_data": os.getenv("TRADINGAGENTS_NEWS_DATA_VENDOR", "aggregated"),
        "macro_data": "fred",
        "prediction_markets": "polymarket",
    }
    cfg["openalgo_host"] = os.getenv("OPENALGO_HOST", "http://127.0.0.1:5001")
    cfg["openalgo_api_key"] = os.getenv("OPENALGO_API_KEY", "")
    cfg["alpaca_profile"] = os.getenv("ALPACA_PROFILE", "paper")
    cfg["alpaca_realtime_enabled"] = (
        os.getenv("ALPACA_REALTIME_ENABLED", "true").strip().lower() in ("1", "true", "yes")
    )
    cfg.setdefault("tool_vendors", {})
    cfg["tool_vendors"]["get_insider_transactions"] = "yfinance,alpha_vantage"


def _patch_vendor_routing() -> None:
    import tradingagents.dataflows.interface as interface
    from trade_integrations.dataflows.news_aggregator import (
        get_global_news_aggregated,
        get_news_aggregated,
    )
    from trade_integrations.dataflows.alpaca import (
        get_alpaca_indicators,
        get_alpaca_stock_data,
    )
    from trade_integrations.dataflows.openalgo import (
        get_openalgo_indicators,
        get_openalgo_stock_data,
    )
    from trade_integrations.dataflows.searxng_news import (
        get_global_news_searxng,
        get_news_searxng,
    )

    for vendor in ("openalgo", "alpaca", "searxng", "aggregated"):
        if vendor not in interface.VENDOR_LIST:
            interface.VENDOR_LIST.append(vendor)

    interface.VENDOR_METHODS["get_stock_data"]["openalgo"] = get_openalgo_stock_data
    interface.VENDOR_METHODS["get_stock_data"]["alpaca"] = get_alpaca_stock_data
    interface.VENDOR_METHODS["get_indicators"]["openalgo"] = get_openalgo_indicators
    interface.VENDOR_METHODS["get_indicators"]["alpaca"] = get_alpaca_indicators
    interface.VENDOR_METHODS["get_news"]["searxng"] = get_news_searxng
    interface.VENDOR_METHODS["get_news"]["aggregated"] = get_news_aggregated
    interface.VENDOR_METHODS["get_global_news"]["searxng"] = get_global_news_searxng
    interface.VENDOR_METHODS["get_global_news"]["aggregated"] = get_global_news_aggregated


def _patch_sentiment_analyst() -> None:
    import tradingagents.agents.analysts.sentiment_analyst as sentiment_module
    from trade_integrations.agents.sentiment_analyst import create_sentiment_analyst

    sentiment_module.create_sentiment_analyst = create_sentiment_analyst


def _patch_news_analyst() -> None:
    import tradingagents.agents.analysts.news_analyst as news_module
    from trade_integrations.agents.news_analyst import create_news_analyst

    news_module.create_news_analyst = create_news_analyst


def _patch_trading_graph() -> None:
    import logging

    from langgraph.prebuilt import ToolNode

    import tradingagents.graph.trading_graph as graph_module
    from trade_integrations.context.hub import (
        prefetch_company_research,
        prefetch_index_research,
        prefetch_options_research,
        prefetch_stock_research,
    )
    from trade_integrations.tools.company_research_tools import get_company_research
    from trade_integrations.tools.index_research_tools import get_index_research
    from trade_integrations.tools.options_research_tools import get_options_research
    from trade_integrations.tools.stock_research_tools import get_stock_research

    logger = logging.getLogger(__name__)
    original_create_tool_nodes = graph_module.TradingAgentsGraph._create_tool_nodes
    original_propagate = graph_module.TradingAgentsGraph.propagate

    def _create_tool_nodes_patched(self):
        tool_nodes = original_create_tool_nodes(self)
        news_tools = list(tool_nodes["news"].tools_by_name.values())
        changed = False
        if get_company_research not in news_tools:
            news_tools.append(get_company_research)
            changed = True
        if get_options_research not in news_tools:
            news_tools.append(get_options_research)
            changed = True
        if get_stock_research not in news_tools:
            news_tools.append(get_stock_research)
            changed = True
        if get_index_research not in news_tools:
            news_tools.append(get_index_research)
            changed = True
        if changed:
            tool_nodes["news"] = ToolNode(news_tools)
        return tool_nodes

    def propagate_patched(self, company_name, trade_date, asset_type: str = "stock"):
        try:
            if prefetch_company_research(company_name, asset_type=asset_type):
                logger.info(
                    "Prefetched company research for %s into trade-stack hub",
                    company_name,
                )
            if prefetch_options_research(company_name):
                logger.info(
                    "Prefetched options research for %s into trade-stack hub",
                    company_name,
                )
            if prefetch_stock_research(company_name):
                logger.info(
                    "Prefetched stock research for %s into trade-stack hub",
                    company_name,
                )
            if prefetch_index_research(company_name):
                logger.info(
                    "Prefetched index research for %s into trade-stack hub",
                    company_name,
                )
        except Exception:
            logger.exception(
                "Company research prefetch failed for %s; continuing graph run",
                company_name,
            )
        return original_propagate(self, company_name, trade_date, asset_type=asset_type)

    graph_module.TradingAgentsGraph._create_tool_nodes = _create_tool_nodes_patched
    graph_module.TradingAgentsGraph.propagate = propagate_patched
