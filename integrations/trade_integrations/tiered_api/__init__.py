"""Unified tiered API queue + hub cache for paid/rate-limited vendors."""

from trade_integrations.tiered_api.client import TieredResult, get_status_all, tiered_fetch
from trade_integrations.tiered_api.errors import (
    TieredApiBudgetExhausted,
    TieredApiDisabledError,
    TieredApiError,
    TieredApiNotConfiguredError,
    TieredApiSourceUnknownError,
)
from trade_integrations.tiered_api.http import tiered_http_get, tiered_http_post_json
from trade_integrations.tiered_api.registry import TIERED_SOURCE_KEYS, list_sources
from trade_integrations.tiered_api.request_key import TieredRequest, request_hash
from trade_integrations.tiered_api.sources.eod_historical import (
    get_eod_historical_daily,
    get_eod_historical_fundamentals,
)

__all__ = [
    "TIERED_SOURCE_KEYS",
    "TieredApiBudgetExhausted",
    "TieredApiDisabledError",
    "TieredApiError",
    "TieredApiNotConfiguredError",
    "TieredApiSourceUnknownError",
    "TieredRequest",
    "TieredResult",
    "get_eod_historical_daily",
    "get_eod_historical_fundamentals",
    "get_status_all",
    "list_sources",
    "request_hash",
    "tiered_fetch",
    "tiered_http_get",
    "tiered_http_post_json",
]
