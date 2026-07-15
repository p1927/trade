"""Tapetide MCP client for Indian stock research (identity, events, peers)."""

from __future__ import annotations

import json
import logging
import os
from typing import Any

import requests

logger = logging.getLogger(__name__)

DEFAULT_MCP_URL = "https://mcp.tapetide.com/mcp"
REQUEST_TIMEOUT = 45


class TapetideNotConfiguredError(RuntimeError):
    """Raised when TAPETIDE_TOKEN is missing."""


def _token() -> str:
    token = os.getenv("TAPETIDE_TOKEN", "").strip() or os.getenv("TAPETIDE_API_TOKEN", "").strip()
    if not token:
        raise TapetideNotConfiguredError(
            "TAPETIDE_TOKEN is not set. Get a free token at https://tapetide.com/settings/tokens"
        )
    return token


def is_configured() -> bool:
    try:
        _token()
        return True
    except TapetideNotConfiguredError:
        return False


def _mcp_url() -> str:
    return os.getenv("TAPETIDE_MCP_URL", DEFAULT_MCP_URL).rstrip("/")


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


def call_tool(tool_name: str, arguments: dict[str, Any]) -> Any:
    """Invoke one Tapetide MCP tool and return parsed result content."""
    response = requests.post(
        _mcp_url(),
        headers={
            "Authorization": f"Bearer {_token()}",
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        },
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": tool_name, "arguments": arguments},
        },
        timeout=REQUEST_TIMEOUT,
    )
    if response.status_code == 401:
        raise TapetideNotConfiguredError("Tapetide token rejected (401). Generate a new token.")
    response.raise_for_status()
    payload = _parse_mcp_body(response.text)
    if "error" in payload:
        err = payload["error"]
        if isinstance(err, dict):
            raise RuntimeError(err.get("message") or err.get("error_description") or str(err))
        raise RuntimeError(str(err))
    result = payload.get("result") or {}
    content = result.get("content") or []
    texts: list[str] = []
    for block in content:
        if isinstance(block, dict) and block.get("type") == "text":
            texts.append(str(block.get("text") or ""))
    combined = "\n".join(t for t in texts if t).strip()
    if not combined:
        return result
    try:
        return json.loads(combined)
    except json.JSONDecodeError:
        return {"raw_text": combined}


def get_company_profile(symbol: str, *, include_peers: bool = True) -> dict[str, Any]:
    include = ["peers"] if include_peers else []
    data = call_tool(
        "get_company_profile",
        {"symbol": symbol.upper(), "include": include},
    )
    return data if isinstance(data, dict) else {"raw": data}


def get_stock_events(symbol: str, *, limit: int = 20) -> dict[str, Any]:
    data = call_tool(
        "get_stock_events",
        {"symbol": symbol.upper(), "limit": limit},
    )
    return data if isinstance(data, dict) else {"raw": data}
