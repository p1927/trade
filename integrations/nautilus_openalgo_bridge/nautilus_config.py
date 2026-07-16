"""Nautilus-specific configuration types for the OpenAlgo bridge."""

from __future__ import annotations

from nautilus_trader.config import LiveDataClientConfig


class OpenAlgoDataClientConfig(LiveDataClientConfig, frozen=True):
    poll_interval_ms: int = 2_000
    watch_symbols: tuple[str, ...] = ("NIFTY", "BANKNIFTY", "INDIAVIX")
