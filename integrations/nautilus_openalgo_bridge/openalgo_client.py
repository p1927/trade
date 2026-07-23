"""OpenAlgo REST client for the Nautilus watch bridge."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any

from nautilus_openalgo_bridge.config import BridgeConfig, get_bridge_config


def _load_base_openalgo_client():
    """Load autonomous_agents OpenAlgoClient without importing trade_integrations package init."""
    path = (
        Path(__file__).resolve().parents[1]
        / "trade_integrations"
        / "execution"
        / "openalgo_client.py"
    )
    spec = importlib.util.spec_from_file_location("_autonomous_agents_openalgo_client", path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load OpenAlgo client from {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.OpenAlgoClient


_BaseOpenAlgoClient = _load_base_openalgo_client()


class BridgeOpenAlgoClient(_BaseOpenAlgoClient):
    """Extends autonomous_agents OpenAlgo client with quote endpoints."""

    def _quote_port(self):
        adapter = getattr(self, "_quote_port_adapter", None)
        if adapter is None:
            from trade_integrations.execution.adapters.openalgo_adapter import OpenAlgoConnectorAdapter

            adapter = OpenAlgoConnectorAdapter(client=self)
            self._quote_port_adapter = adapter
        return adapter

    def get_quote(self, symbol: str, *, exchange: str = "NSE") -> dict[str, Any]:
        """Single quote via TradingConnectorPort (OpenAlgo adapter)."""
        quote = self._quote_port().quote(symbol, exchange=exchange)
        return quote if isinstance(quote, dict) else {}

    def get_multi_quotes(self, symbols: list[dict[str, str]]) -> dict[str, Any]:
        """Batch quotes via TradingConnectorPort (OpenAlgo adapter)."""
        normalized = [
            {"symbol": row["symbol"].upper(), "exchange": row["exchange"].upper()}
            for row in symbols
            if isinstance(row, dict) and row.get("symbol") and row.get("exchange")
        ]
        batch = self._quote_port().quotes_batch(normalized)
        quotes: list[dict[str, Any]] = []
        for req in normalized:
            key = f"{req['symbol']}@{req['exchange']}"
            row = batch.get(key)
            if isinstance(row, dict):
                quotes.append({**row, "symbol": req["symbol"], "exchange": req["exchange"]})
        return {"quotes": quotes}

    def get_symbol_info(self, symbol: str, *, exchange: str = "NFO") -> dict[str, Any]:
        body = self._post(
            "symbol",
            {"apikey": self.api_key, "symbol": symbol.upper(), "exchange": exchange.upper()},
            timeout=15,
        )
        data = body.get("data")
        return data if isinstance(data, dict) else body

    def place_order(self, order: dict[str, Any], *, strategy: str = "autonomous_agents") -> dict[str, Any]:
        payload = {"apikey": self.api_key, "strategy": strategy, **order}
        body = self._post("placeorder", payload, timeout=45)
        data = body.get("data") if isinstance(body.get("data"), dict) else body
        return data if isinstance(data, dict) else body

    def cancel_order(self, order_id: str, *, strategy: str = "autonomous_agents") -> dict[str, Any]:
        body = self._post(
            "cancelorder",
            {"apikey": self.api_key, "strategy": strategy, "orderid": str(order_id)},
            timeout=30,
        )
        data = body.get("data") if isinstance(body.get("data"), dict) else body
        return data if isinstance(data, dict) else body

    def get_orderbook(self) -> list[dict[str, Any]]:
        body = self._post("orderbook", {"apikey": self.api_key}, timeout=15)
        rows = body.get("data")
        if isinstance(rows, list):
            return [row for row in rows if isinstance(row, dict)]
        return []

    def get_expiry_dates(
        self,
        symbol: str,
        *,
        exchange: str = "NFO",
        instrument_type: str = "options",
    ) -> list[str]:
        body = self._post(
            "expiry",
            {
                "apikey": self.api_key,
                "symbol": symbol.upper(),
                "exchange": exchange.upper(),
                "instrumenttype": instrument_type,
            },
            timeout=15,
        )
        data = body.get("data")
        if isinstance(data, list):
            return [str(x) for x in data]
        if isinstance(data, dict):
            return list(data.get("expiry_dates") or data.get("expiries") or [])
        return []

    @staticmethod
    def _normalize_expiry(expiry: str) -> str:
        return expiry.strip().upper().replace("-", "")

    def get_option_chain(
        self,
        underlying: str,
        *,
        exchange: str = "NFO",
        expiry_date: str | None = None,
        strike_count: int = 5,
    ) -> dict[str, Any]:
        expiry = expiry_date
        if not expiry:
            dates = self.get_expiry_dates(underlying, exchange=exchange)
            if not dates:
                raise RuntimeError(f"no expiries for {underlying}")
            expiry = dates[0]
        payload: dict[str, Any] = {
            "apikey": self.api_key,
            "underlying": underlying.upper(),
            "exchange": exchange.upper(),
            "expiry_date": self._normalize_expiry(expiry),
            "strike_count": strike_count,
        }
        body = self._post("optionchain", payload, timeout=30)
        data = body.get("data") if isinstance(body.get("data"), dict) else body
        return data if isinstance(data, dict) else {"chain": body.get("chain") or []}


def get_openalgo_client(config: BridgeConfig | None = None) -> BridgeOpenAlgoClient:
    cfg = config or get_bridge_config()
    return BridgeOpenAlgoClient(host=cfg.openalgo_host, api_key=cfg.openalgo_api_key)
