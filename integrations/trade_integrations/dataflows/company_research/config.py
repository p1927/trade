"""Configuration for the company research pipeline."""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class ResearchConfig:
    lookahead_days: int = 14
    max_peers: int = 8
    news_lookback_days: int = 7
    market_default: str = "IN"
    enabled_stages: tuple[str, ...] = (
        "market",
        "identity",
        "peers",
        "calendar",
        "fundamentals",
        "filings",
        "news",
        "sentiment",
        "corp_events",
        "earnings_signal",
        "macro",
    )


def get_research_config() -> ResearchConfig:
    """Load research settings from environment with sensible defaults."""
    return ResearchConfig(
        lookahead_days=int(os.getenv("TRADINGAGENTS_RESEARCH_LOOKAHEAD_DAYS", "14")),
        max_peers=int(os.getenv("TRADINGAGENTS_RESEARCH_MAX_PEERS", "8")),
        news_lookback_days=int(os.getenv("TRADINGAGENTS_RESEARCH_NEWS_LOOKBACK_DAYS", "7")),
        market_default=os.getenv("TRADINGAGENTS_RESEARCH_MARKET_DEFAULT", "IN").upper(),
    )
