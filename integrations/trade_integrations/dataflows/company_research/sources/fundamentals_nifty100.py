"""Nifty 100 financial intelligence fundamentals — hub cache lookup."""

from __future__ import annotations

from typing import Any

from trade_integrations.dataflows.nifty100_financial_intel.ingest import load_symbol_fundamentals


def fetch_fundamentals_nifty100(nse_symbol: str) -> dict[str, Any] | None:
    """Return fundamentals from ingested Nifty 100 GitHub dataset for an NSE symbol."""
    data = load_symbol_fundamentals(nse_symbol)
    if not data:
        return None
    return {
        "source": "nifty100_financial_intel",
        **data,
    }
