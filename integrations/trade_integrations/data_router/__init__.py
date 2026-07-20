"""Unified data fetch router."""

from trade_integrations.data_router.router import data_router_enabled, fetch, get_status
from trade_integrations.data_router.types import FetchResult, FetchSpec, SourceAttempt

__all__ = [
    "FetchResult",
    "FetchSpec",
    "SourceAttempt",
    "data_router_enabled",
    "fetch",
    "get_status",
]
