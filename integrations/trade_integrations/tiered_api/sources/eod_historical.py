"""EOD Historical Data API client routed through tiered_fetch."""

from __future__ import annotations

import logging
from typing import Any

from trade_integrations.tiered_api.http import tiered_http_get

logger = logging.getLogger(__name__)

BASE_URL = "https://eodhistoricaldata.com/api"


def _symbol_exchange(symbol: str, exchange: str = "NSE") -> str:
    sym = symbol.strip().upper()
    ex = exchange.strip().upper()
    if "." in sym:
        return sym
    return f"{sym}.{ex}"


def get_eod_historical_daily(
    symbol: str,
    *,
    exchange: str = "NSE",
    start: str | None = None,
    end: str | None = None,
    force: bool = False,
) -> list[dict[str, Any]]:
    """Fetch daily EOD OHLCV bars for a symbol via EOD Historical Data API."""
    ticker = _symbol_exchange(symbol, exchange)
    url = f"{BASE_URL}/eod/{ticker}"
    params: dict[str, Any] = {"fmt": "json"}
    if start:
        params["from"] = start
    if end:
        params["to"] = end

    payload = tiered_http_get(
        "eod_historical",
        url,
        params=params,
        credential_param="api_token",
        force=force,
    )
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        return [payload]
    return []


def get_eod_historical_fundamentals(
    symbol: str,
    *,
    exchange: str = "NSE",
    force: bool = False,
) -> dict[str, Any]:
    """Fetch fundamentals snapshot for a symbol."""
    ticker = _symbol_exchange(symbol, exchange)
    url = f"{BASE_URL}/fundamentals/{ticker}"
    payload = tiered_http_get(
        "eod_historical",
        url,
        params={"fmt": "json"},
        credential_param="api_token",
        force=force,
    )
    return payload if isinstance(payload, dict) else {"raw": payload}
