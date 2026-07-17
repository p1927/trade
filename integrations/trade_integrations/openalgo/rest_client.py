"""Canonical OpenAlgo REST client for market-data and execution paths."""

from __future__ import annotations

import logging
import os
import time
from typing import Any

from trade_integrations.env import ensure_openalgo_env, load_trade_env

logger = logging.getLogger(__name__)

_TRANSIENT_STATUS = {502, 503, 504}


def openalgo_settings(*, root=None) -> tuple[str, str]:
    """Return (host, api_key) from env / stack ports (no TradingAgents import)."""
    load_trade_env(root=root)
    cfg = ensure_openalgo_env(root=root)
    host = (os.getenv("OPENALGO_HOST") or cfg["host"]).rstrip("/")
    api_key = (os.getenv("OPENALGO_API_KEY") or cfg["api_key"]).strip()
    return host, api_key


class OpenAlgoRestClient:
    """Thin OpenAlgo REST wrapper with retries on transient HTTP failures."""

    def __init__(self, host: str | None = None, api_key: str | None = None) -> None:
        default_host, default_key = openalgo_settings()
        self.host = (host or default_host).rstrip("/")
        self.api_key = (api_key or default_key).strip()
        if not self.api_key:
            raise RuntimeError("OPENALGO_API_KEY not configured")

    def post(self, path: str, payload: dict[str, Any], *, timeout: int = 30) -> dict[str, Any]:
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


def get_rest_client(
    host: str | None = None,
    api_key: str | None = None,
) -> OpenAlgoRestClient:
    """Return an OpenAlgo REST client (optionally overriding host/api_key)."""
    return OpenAlgoRestClient(host=host, api_key=api_key)
