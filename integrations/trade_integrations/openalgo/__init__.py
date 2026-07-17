"""Unified OpenAlgo access layer (REST client, market data — see submodules)."""

from trade_integrations.openalgo.rest_client import (
    OpenAlgoRestClient,
    get_rest_client,
    openalgo_settings,
)

__all__ = [
    "OpenAlgoRestClient",
    "get_rest_client",
    "openalgo_settings",
]
