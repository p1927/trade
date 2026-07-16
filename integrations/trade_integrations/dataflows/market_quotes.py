"""Market-aware live quote router: OpenAlgo (IN) + Alpaca (US)."""

from __future__ import annotations

import logging

from trade_integrations.dataflows.company_research.market import Market, detect_market

logger = logging.getLogger(__name__)


def fetch_live_quote(symbol: str) -> dict | None:
    """Return a normalized live quote for Indian or US equities."""
    raw = symbol.strip().upper()
    if not raw:
        return None

    market = detect_market(raw)

    if market is Market.IN:
        try:
            from trade_integrations.dataflows.openalgo import fetch_openalgo_quote

            return fetch_openalgo_quote(raw)
        except Exception:
            logger.debug("openalgo quote failed for %s", raw, exc_info=True)
            return None

    try:
        from trade_integrations.dataflows.alpaca import (
            alpaca_configured,
            fetch_alpaca_quote,
            fetch_alpaca_trade_snapshot,
        )

        if not alpaca_configured():
            return None
        quote = fetch_alpaca_quote(raw)
        if quote and quote.get("ltp") is not None:
            return quote
        return fetch_alpaca_trade_snapshot(raw)
    except Exception:
        logger.debug("alpaca quote failed for %s", raw, exc_info=True)
        return None
