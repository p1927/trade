"""Market-aware live quote router: OpenAlgo for IN and US (Alpaca plugin)."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Literal

from trade_integrations.dataflows.company_research.market import Market, detect_market

if TYPE_CHECKING:
    from trade_integrations.execution.connector_context import ConnectorExecutionContext

logger = logging.getLogger(__name__)

QuoteMarket = Literal["IN", "US"]


def _load_context_optional() -> ConnectorExecutionContext | None:
    try:
        from trade_integrations.execution.connector_context import load_active_connector_context

        return load_active_connector_context()
    except Exception:
        logger.debug("connector context unavailable for market_quotes", exc_info=True)
        return None


def resolve_quote_market(
    symbol: str,
    connector_context: ConnectorExecutionContext | None = None,
) -> QuoteMarket:
    """Resolve quote backend from connector context, else symbol registry."""
    ctx = connector_context if connector_context is not None else _load_context_optional()
    if ctx is not None:
        return ctx.market
    detected = detect_market(symbol.strip().upper())
    return "IN" if detected is Market.IN else "US"


def fetch_live_quote(
    symbol: str,
    *,
    agent: dict | None = None,
    connector_context: ConnectorExecutionContext | None = None,
) -> dict | None:
    """Return a normalized live quote for Indian or US equities."""
    raw = symbol.strip().upper()
    if not raw:
        return None

    if agent is not None:
        try:
            from trade_integrations.execution.trading_port import adapter_for_agent

            return adapter_for_agent(agent).quote(raw)
        except Exception:
            logger.debug("adapter quote failed for %s; falling back to connector path", raw, exc_info=True)

    market = resolve_quote_market(raw, connector_context)
    use_openalgo = market in ("IN", "US")

    if use_openalgo:
        try:
            from trade_integrations.openalgo.market_data import fetch_openalgo_quote

            return fetch_openalgo_quote(raw)
        except Exception:
            logger.debug("openalgo quote failed for %s", raw, exc_info=True)
            return None

    return None
