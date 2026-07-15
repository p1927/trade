"""Configuration for the options research pipeline."""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class OptionsResearchConfig:
    lookahead_days: int = 14
    strike_count: int = 15
    cache_minutes: int = 30
    prefetch: bool = True
    broker_preset: str = "zerodha"
    enabled_stages: tuple[str, ...] = (
        "market",
        "chain",
        "events",
        "analytics",
        "candidates",
        "rank",
        "payoff",
    )


def get_options_config() -> OptionsResearchConfig:
    """Load options research settings from environment."""
    prefetch_raw = os.getenv("TRADINGAGENTS_OPTIONS_PREFETCH", "true").strip().lower()
    return OptionsResearchConfig(
        lookahead_days=int(os.getenv("TRADINGAGENTS_OPTIONS_LOOKAHEAD_DAYS", "14")),
        strike_count=int(os.getenv("TRADINGAGENTS_OPTIONS_STRIKE_COUNT", "15")),
        cache_minutes=int(os.getenv("TRADINGAGENTS_OPTIONS_CACHE_MINUTES", "30")),
        prefetch=prefetch_raw not in {"0", "false", "no", "off"},
        broker_preset=os.getenv("TRADINGAGENTS_OPTIONS_BROKER_PRESET", "zerodha").lower(),
    )
