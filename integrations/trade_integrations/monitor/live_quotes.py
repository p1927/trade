"""Live underlying quotes for the options plan monitor."""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def fetch_underlying_ltp(ticker: str) -> float | None:
    """Fetch last traded price for an underlying; None on failure."""
    try:
        from trade_integrations.dataflows.openalgo import fetch_openalgo_quote
    except ImportError:
        logger.debug("openalgo adapter unavailable for %s", ticker)
        return None

    try:
        quote = fetch_openalgo_quote(ticker)
    except Exception:
        logger.debug("failed to fetch live quote for %s", ticker, exc_info=True)
        return None

    if not quote:
        return None

    ltp = quote.get("ltp")
    if ltp is None:
        return None
    try:
        return float(ltp)
    except (TypeError, ValueError):
        return None
