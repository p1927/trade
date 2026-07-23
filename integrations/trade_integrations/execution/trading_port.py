"""Hexagonal trading port — quotes, orders, positions, market context."""

from __future__ import annotations

from typing import Protocol

from trade_integrations.openalgo.market_context import MarketContext


class TradingConnectorPort(Protocol):
    """Single interface for connector-backed market data and execution."""

    def market_context(self) -> MarketContext: ...

    def quote(self, symbol: str, exchange: str = "NSE") -> dict | None: ...

    def quotes_batch(self, requests: list[dict]) -> dict[str, dict]: ...

    def positionbook(self) -> list[dict]: ...

    def place_basket(self, legs: list[dict], **kwargs) -> dict: ...

    # Optional: connectors may expose a live watch feed (see watch_feed.WatchFeedHandle).
    # def watch_feed(self, *, context_generation: str = "") -> WatchFeedHandle: ...


def adapter_for_agent(agent: dict) -> TradingConnectorPort:
    """Return the connector adapter for an autonomous agent record."""
    from trade_integrations.execution.adapters.openalgo_adapter import OpenAlgoConnectorAdapter
    from trade_integrations.execution.connector_context import load_active_connector_context

    ctx = load_active_connector_context(agent=agent)
    if ctx is None or ctx.execution_path in ("openalgo", "alpaca_sdk"):
        return OpenAlgoConnectorAdapter()
    raise NotImplementedError(
        f"TradingConnectorPort adapter not implemented "
        f"(execution_path={ctx.execution_path}, connector={ctx.connector}, "
        f"profile={ctx.profile_id})"
    )
