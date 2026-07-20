"""Tapetide MCP client for Indian stock research (identity, events, peers)."""

from __future__ import annotations

import json
import logging
import os
import time
from typing import Any

import requests

from trade_integrations.tiered_api import TieredRequest, tiered_fetch
from trade_integrations.tiered_api.errors import TieredApiBudgetExhausted, TieredApiDisabledError

logger = logging.getLogger(__name__)

DEFAULT_MCP_URL = "https://mcp.tapetide.com/mcp"
REQUEST_TIMEOUT = 45

_rate_limited_until: float = 0.0


class TapetideNotConfiguredError(RuntimeError):
    """Raised when TAPETIDE_TOKEN is missing."""


class TapetideRateLimitError(RuntimeError):
    """Raised when Tapetide free-tier or hourly quota is exhausted."""


def _token() -> str:
    from trade_integrations.tiered_api.registry import resolve_credential

    try:
        return resolve_credential("tapetide")
    except Exception as exc:
        raise TapetideNotConfiguredError(
            "TAPETIDE_TOKEN is not set. Get a free token at https://tapetide.com/settings/tokens"
        ) from exc


def is_enabled() -> bool:
    """Tapetide is always enabled when a token is configured."""
    return True


def is_configured() -> bool:
    from trade_integrations.tiered_api.registry import is_configured as tiered_configured

    return tiered_configured("tapetide")


def is_rate_limited() -> bool:
    return time.monotonic() < _rate_limited_until


def is_active(*, batch: bool | None = None) -> bool:
    """Tapetide is attempted whenever TAPETIDE_TOKEN is set (failures handled per call)."""
    return is_configured()


def _mcp_url() -> str:
    return os.getenv("TAPETIDE_MCP_URL", DEFAULT_MCP_URL).rstrip("/")


def is_rate_limit_message(text: str) -> bool:
    lowered = text.lower()
    return any(
        marker in lowered
        for marker in (
            "rate limit",
            "free tier limit",
            "quota exceeded",
            "too many requests",
        )
    )


def _mark_rate_limited(text: str = "") -> None:
    global _rate_limited_until
    _rate_limited_until = time.monotonic() + 3600.0
    logger.warning("Tapetide rate limit detected; pausing MCP calls for 1 hour. %s", text[:120])


def _check_response_rate_limit(response: requests.Response, combined_text: str = "") -> None:
    if response.status_code == 429 or is_rate_limit_message(combined_text):
        _mark_rate_limited(combined_text)
        raise TapetideRateLimitError(combined_text or "Tapetide rate limit exceeded")


def _parse_mcp_body(text: str) -> dict[str, Any]:
    """Parse JSON or SSE (data: {...}) MCP responses."""
    text = text.strip()
    if not text:
        return {}
    if text.startswith("{"):
        return json.loads(text)
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("data:"):
            payload = line[5:].strip()
            if payload and payload != "[DONE]":
                return json.loads(payload)
    return json.loads(text)


def _execute_mcp_call(tool_name: str, arguments: dict[str, Any]) -> Any:
    """Direct HTTP MCP call (invoked only on tiered_fetch hub miss)."""
    from trade_integrations.dataflows import source_availability

    if not source_availability.should_attempt("tapetide", "api"):
        raise TapetideRateLimitError("Tapetide circuit open; retry later.")

    body = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {"name": tool_name, "arguments": arguments},
    }
    response = requests.post(
        _mcp_url(),
        headers={
            "Authorization": f"Bearer {_token()}",
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        },
        json=body,
        timeout=REQUEST_TIMEOUT,
    )
    if response.status_code == 401:
        err = TapetideNotConfiguredError("Tapetide token rejected (401). Generate a new token.")
        source_availability.record_failure("tapetide", "api", err)
        raise err
    if response.status_code == 429:
        source_availability.record_failure("tapetide", "api", response.text or "429 Too Many Requests")
        _check_response_rate_limit(response, response.text)
    response.raise_for_status()
    payload = _parse_mcp_body(response.text)
    if "error" in payload:
        err = payload["error"]
        message = err.get("message") or err.get("error_description") or str(err) if isinstance(err, dict) else str(err)
        if is_rate_limit_message(message):
            _check_response_rate_limit(response, message)
        raise RuntimeError(message)
    result = payload.get("result") or {}
    content = result.get("content") or []
    texts: list[str] = []
    for block in content:
        if isinstance(block, dict) and block.get("type") == "text":
            texts.append(str(block.get("text") or ""))
    combined = "\n".join(t for t in texts if t).strip()
    if is_rate_limit_message(combined):
        _check_response_rate_limit(response, combined)
    if not combined:
        return result
    try:
        parsed = json.loads(combined)
        if isinstance(parsed, dict):
            return parsed
        return {"raw": parsed}
    except json.JSONDecodeError:
        return {"raw_text": combined}


def call_tool(tool_name: str, arguments: dict[str, Any]) -> Any:
    """Invoke one Tapetide MCP tool via tiered queue + hub cache."""
    from trade_integrations.dataflows import source_availability

    if is_rate_limited():
        raise TapetideRateLimitError("Tapetide calls paused after rate limit (retry in ~1 hour).")

    if not source_availability.should_attempt("tapetide", "api"):
        raise TapetideRateLimitError("Tapetide circuit open; retry later.")

    body = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {"name": tool_name, "arguments": arguments},
    }
    req = TieredRequest(
        method="POST",
        url=_mcp_url(),
        body=json.dumps(body, sort_keys=True, separators=(",", ":")),
        extra={"tool": tool_name, "arguments": arguments},
    )

    try:
        result = tiered_fetch(
            "tapetide",
            req,
            lambda: _execute_mcp_call(tool_name, arguments),
        )
        source_availability.record_success("tapetide", "api")
        return result.data
    except TieredApiBudgetExhausted as exc:
        _mark_rate_limited(str(exc))
        source_availability.record_failure("tapetide", "api", exc)
        raise TapetideRateLimitError(str(exc)) from exc
    except TapetideNotConfiguredError as exc:
        source_availability.record_failure("tapetide", "api", exc)
        raise
    except TapetideRateLimitError as exc:
        source_availability.record_failure("tapetide", "api", exc)
        raise
    except TieredApiDisabledError:
        raise


def get_company_profile(symbol: str, *, include_peers: bool = True) -> dict[str, Any]:
    symbol_upper = symbol.upper()
    data = call_tool(
        "get_company_profile",
        {"symbol": symbol_upper, "include": ["peers"]},
    )
    result = data if isinstance(data, dict) else {"raw": data}
    if result.get("raw_text") and is_rate_limit_message(str(result["raw_text"])):
        _mark_rate_limited(str(result["raw_text"]))
        raise TapetideRateLimitError(str(result["raw_text"]))
    return result


def get_stock_events(symbol: str, *, limit: int = 20) -> dict[str, Any]:
    symbol_upper = symbol.upper()
    data = call_tool(
        "get_stock_events",
        {"symbol": symbol_upper, "limit": limit},
    )
    result = data if isinstance(data, dict) else {"raw": data}
    if result.get("raw_text") and is_rate_limit_message(str(result["raw_text"])):
        _mark_rate_limited(str(result["raw_text"]))
        raise TapetideRateLimitError(str(result["raw_text"]))
    return result
