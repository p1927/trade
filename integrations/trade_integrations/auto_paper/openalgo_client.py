"""Thin OpenAlgo REST client for automated paper trading."""

from __future__ import annotations

import logging
import time
from typing import Any

from trade_integrations.env import ensure_openalgo_env

logger = logging.getLogger(__name__)

_TRANSIENT_STATUS = {502, 503, 504}


class OpenAlgoClient:
    def __init__(self, host: str | None = None, api_key: str | None = None) -> None:
        cfg = ensure_openalgo_env()
        self.host = (host or cfg["host"]).rstrip("/")
        self.api_key = (api_key or cfg["api_key"]).strip()
        if not self.api_key:
            raise RuntimeError("OPENALGO_API_KEY not configured")

    def _post(self, path: str, payload: dict[str, Any], *, timeout: int = 30) -> dict[str, Any]:
        import requests

        url = f"{self.host}/api/v1/{path.lstrip('/')}"
        last_exc: Exception | None = None
        for attempt in range(2):
            try:
                response = requests.post(url, json=payload, timeout=timeout)
                body = response.json() if response.content else {}
            except requests.RequestException as exc:
                last_exc = exc
                if attempt == 0:
                    time.sleep(1.0)
                    continue
                logger.warning("OpenAlgo %s failed: %s", path, exc)
                raise RuntimeError(f"OpenAlgo request failed: {exc}") from exc
            if response.ok:
                return body if isinstance(body, dict) else {"data": body}
            message = body.get("message") if isinstance(body, dict) else str(body)
            code = body.get("error_code") if isinstance(body, dict) else None
            if response.status_code in _TRANSIENT_STATUS and attempt == 0:
                time.sleep(1.0)
                continue
            if code == "invalid_api_key":
                raise RuntimeError(message or "Invalid OpenAlgo API key")
            raise RuntimeError(message or f"OpenAlgo {path} HTTP {response.status_code}")
        if last_exc is not None:
            raise RuntimeError(f"OpenAlgo request failed: {last_exc}") from last_exc
        raise RuntimeError(f"OpenAlgo {path} failed")

    def analyzer_status(self) -> bool:
        body = self._post("analyzer", {"apikey": self.api_key}, timeout=15)
        data = body.get("data") if isinstance(body.get("data"), dict) else body
        return bool(data.get("analyze_mode"))

    def ensure_analyzer_mode(self) -> bool:
        """Enable paper/analyzer mode idempotently."""
        if self.analyzer_status():
            return True
        body = self._post("analyzer/toggle", {"apikey": self.api_key, "mode": True}, timeout=15)
        data = body.get("data") if isinstance(body.get("data"), dict) else body
        return bool(data.get("analyze_mode", True))

    def get_funds(self) -> dict[str, Any]:
        body = self._post("funds", {"apikey": self.api_key}, timeout=15)
        data = body.get("data")
        return data if isinstance(data, dict) else body

    def calculate_margin(self, positions: list[dict[str, Any]]) -> float | None:
        if not positions:
            return None
        body = self._post(
            "margin",
            {"apikey": self.api_key, "positions": positions},
            timeout=20,
        )
        data = body.get("data") if isinstance(body.get("data"), dict) else body
        for key in ("totalmargin", "total_margin", "margin"):
            value = data.get(key)
            if value is not None:
                try:
                    return float(value)
                except (TypeError, ValueError):
                    continue
        return None

    def place_basket(self, orders: list[dict[str, Any]], *, strategy: str = "auto_paper") -> list[dict[str, Any]]:
        body = self._post(
            "basketorder",
            {"apikey": self.api_key, "strategy": strategy, "orders": orders},
            timeout=45,
        )
        results = body.get("results") or body.get("data") or []
        if isinstance(results, dict):
            return [results]
        return results if isinstance(results, list) else []

    def close_all_positions(self, *, strategy: str = "auto_paper") -> dict[str, Any]:
        return self._post(
            "closeposition",
            {"apikey": self.api_key, "strategy": strategy},
            timeout=45,
        )

    def get_position_book(self) -> list[dict[str, Any]]:
        body = self._post("positionbook", {"apikey": self.api_key}, timeout=15)
        rows = body.get("data")
        if isinstance(rows, list):
            return [row for row in rows if isinstance(row, dict)]
        return []

    def is_trading_day(self) -> bool:
        from datetime import date

        today = date.today().isoformat()
        body = self._post(
            "market/timings",
            {"apikey": self.api_key, "date": today},
            timeout=15,
        )
        data = body.get("data")
        if isinstance(data, dict):
            if "is_trading_day" in data:
                return bool(data["is_trading_day"])
            exchanges = data.get("exchanges") or data.get("NSE")
            if isinstance(exchanges, dict):
                nse = exchanges.get("NSE") or exchanges.get("nse")
                if isinstance(nse, dict) and "is_open" in nse:
                    return True
        return True
