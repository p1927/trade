"""Trade-stack runtime hooks for the TradingAgents submodule."""

from __future__ import annotations

import os

_APPLIED = False


def apply() -> None:
    """Register OpenAlgo, SearXNG, RSS, and news-aggregator integrations."""
    global _APPLIED
    if _APPLIED:
        return
    _APPLIED = True

    _patch_default_config()
    _patch_vendor_routing()
    _patch_sentiment_analyst()


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
    cfg["data_vendors"] = {
        "core_stock_apis": os.getenv(
            "TRADINGAGENTS_CORE_STOCK_DATA_VENDOR",
            "openalgo,yfinance" if os.getenv("OPENALGO_API_KEY") else "yfinance",
        ),
        "technical_indicators": os.getenv(
            "TRADINGAGENTS_TECHNICAL_INDICATORS_DATA_VENDOR",
            "openalgo,yfinance" if os.getenv("OPENALGO_API_KEY") else "yfinance",
        ),
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
    cfg.setdefault("tool_vendors", {})
    cfg["tool_vendors"]["get_insider_transactions"] = "yfinance,alpha_vantage"


def _patch_vendor_routing() -> None:
    import tradingagents.dataflows.interface as interface
    from trade_integrations.dataflows.news_aggregator import (
        get_global_news_aggregated,
        get_news_aggregated,
    )
    from trade_integrations.dataflows.openalgo import (
        get_openalgo_indicators,
        get_openalgo_stock_data,
    )
    from trade_integrations.dataflows.searxng_news import (
        get_global_news_searxng,
        get_news_searxng,
    )

    for vendor in ("openalgo", "searxng", "aggregated"):
        if vendor not in interface.VENDOR_LIST:
            interface.VENDOR_LIST.append(vendor)

    interface.VENDOR_METHODS["get_stock_data"]["openalgo"] = get_openalgo_stock_data
    interface.VENDOR_METHODS["get_indicators"]["openalgo"] = get_openalgo_indicators
    interface.VENDOR_METHODS["get_news"]["searxng"] = get_news_searxng
    interface.VENDOR_METHODS["get_news"]["aggregated"] = get_news_aggregated
    interface.VENDOR_METHODS["get_global_news"]["searxng"] = get_global_news_searxng
    interface.VENDOR_METHODS["get_global_news"]["aggregated"] = get_global_news_aggregated


def _patch_sentiment_analyst() -> None:
    import tradingagents.agents.analysts.sentiment_analyst as sentiment_module
    from trade_integrations.agents.sentiment_analyst import create_sentiment_analyst

    sentiment_module.create_sentiment_analyst = create_sentiment_analyst
