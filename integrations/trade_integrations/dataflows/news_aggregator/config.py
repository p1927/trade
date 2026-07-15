"""Aggregator-specific configuration."""

from __future__ import annotations

import os

from tradingagents.dataflows.config import get_config

DEFAULT_SOURCES = "searxng,yfinance,alpha_vantage"
SUPPORTED_SOURCES = frozenset({"searxng", "yfinance", "alpha_vantage"})
# Fetch more upstream than we emit so dedup/selection have a rich pool.
FETCH_MULTIPLIER = 3


def get_aggregator_sources() -> list[str]:
    """Return the ordered list of news backends to merge."""
    raw = os.environ.get(
        "TRADINGAGENTS_NEWS_AGGREGATOR_SOURCES",
        get_config().get("news_aggregator_sources", DEFAULT_SOURCES),
    )
    sources = [s.strip() for s in raw.split(",") if s.strip()]
    return [s for s in sources if s in SUPPORTED_SOURCES]
