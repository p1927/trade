"""Registry of per-vendor fetch adapters."""

from __future__ import annotations

from . import alpha_vantage, searxng, yfinance

SOURCE_REGISTRY = {
    "searxng": searxng,
    "yfinance": yfinance,
    "alpha_vantage": alpha_vantage,
}
