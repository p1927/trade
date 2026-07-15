"""Multi-source news aggregation for TradingAgents.

Fetches from SearXNG, yfinance, and Alpha Vantage in parallel (best-effort),
deduplicates by URL/title, filters by date window, and returns the unified
markdown reports consumed by agent tools.
"""

from .aggregator import get_global_news_aggregated, get_news_aggregated

__all__ = [
    "get_global_news_aggregated",
    "get_news_aggregated",
]
