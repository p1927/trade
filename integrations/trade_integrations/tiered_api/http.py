"""HTTP helpers routed through tiered_fetch."""

from __future__ import annotations

import json
import logging
from typing import Any

import requests

from trade_integrations.tiered_api.client import tiered_fetch
from trade_integrations.tiered_api.registry import resolve_credential
from trade_integrations.tiered_api.request_key import TieredRequest, build_url

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT = 45
DEFAULT_UA = "trade-stack-research/0.1 (+https://github.com/p1927/trade)"


def tiered_http_get(
    source: str,
    url: str,
    *,
    params: dict[str, Any] | None = None,
    timeout: float = DEFAULT_TIMEOUT,
    headers: dict[str, str] | None = None,
    force: bool = False,
    inject_credential: bool = True,
    credential_param: str | None = None,
) -> Any:
    """GET JSON/text via tiered_fetch with hub cache + queue."""
    params = dict(params or {})
    req = TieredRequest(method="GET", url=url, params=params)

    def _fetch() -> Any:
        call_params = dict(params)
        if inject_credential:
            cred = resolve_credential(source)
            param_name = credential_param or _default_cred_param(source)
            call_params.setdefault(param_name, cred)
        hdrs = {"User-Agent": DEFAULT_UA, **(headers or {})}
        resp = requests.get(url, params=call_params, headers=hdrs, timeout=timeout)
        resp.raise_for_status()
        text = resp.text
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return text

    result = tiered_fetch(source, req, _fetch, force=force)
    return result.data


def tiered_http_post_json(
    source: str,
    url: str,
    *,
    json_body: dict[str, Any],
    headers: dict[str, str] | None = None,
    timeout: float = DEFAULT_TIMEOUT,
    force: bool = False,
    auth_bearer: bool = True,
) -> Any:
    """POST JSON via tiered_fetch (e.g. Tapetide MCP)."""
    body_str = json.dumps(json_body, sort_keys=True, separators=(",", ":"))
    req = TieredRequest(method="POST", url=url, body=body_str)

    def _fetch() -> Any:
        hdrs = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            **(headers or {}),
        }
        if auth_bearer:
            hdrs["Authorization"] = f"Bearer {resolve_credential(source)}"
        resp = requests.post(url, json=json_body, headers=hdrs, timeout=timeout)
        resp.raise_for_status()
        return resp.text

    result = tiered_fetch(source, req, _fetch, force=force)
    return result.data


def _default_cred_param(source: str) -> str:
    mapping = {
        "alpha_vantage": "apikey",
        "eod_historical": "api_token",
        "finnhub": "token",
        "fmp": "apikey",
        "tiingo": "token",
    }
    return mapping.get(source.strip().lower(), "api_key")
