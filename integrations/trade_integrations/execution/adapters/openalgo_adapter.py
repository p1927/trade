"""OpenAlgo implementation of TradingConnectorPort."""

from __future__ import annotations

from typing import Any

from trade_integrations.execution.openalgo_client import OpenAlgoClient
from trade_integrations.openalgo.market_context import MarketContext


class OpenAlgoConnectorAdapter:
    """India-path adapter wrapping autonomous_agents OpenAlgoClient and market_data helpers."""

    def __init__(
        self,
        *,
        host: str | None = None,
        api_key: str | None = None,
        client: OpenAlgoClient | None = None,
    ) -> None:
        self._client = client or OpenAlgoClient(host=host, api_key=api_key)

    def market_context(self) -> MarketContext:
        return self._client.get_market_context()

    def quote(self, symbol: str, exchange: str | None = None) -> dict | None:
        from trade_integrations.openalgo.market_data import fetch_quote_raw
        from trade_integrations.openalgo.symbols import resolve_openalgo_symbol

        oa_symbol, resolved_exchange = resolve_openalgo_symbol(symbol)
        return fetch_quote_raw(oa_symbol, exchange=exchange or resolved_exchange)

    def quotes_batch(self, requests: list[dict]) -> dict[str, dict]:
        from trade_integrations.openalgo.market_data import (
            fetch_multi_quotes_raw,
            parse_multi_quotes_payload,
        )

        payload = fetch_multi_quotes_raw(requests)
        return parse_multi_quotes_payload(payload)

    def positionbook(self) -> list[dict]:
        return self._client.get_position_book()

    def place_basket(self, legs: list[dict], **kwargs: Any) -> dict:
        strategy = str(kwargs.get("strategy") or "autonomous_agents")
        results = self._client.place_basket(legs, strategy=strategy)
        return {"status": "success", "results": results}
