"""Tapetide MCP client for Indian stock research (identity, events, peers)."""

from __future__ import annotations

import json
import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests

logger = logging.getLogger(__name__)

DEFAULT_MCP_URL = "https://mcp.tapetide.com/mcp"
REQUEST_TIMEOUT = 45
PROFILE_CACHE_TTL_SEC = 15 * 60
DISK_CACHE_DEFAULT_MINUTES = 60

_profile_cache: dict[str, tuple[float, dict[str, Any]]] = {}
_events_cache: dict[str, tuple[float, dict[str, Any]]] = {}
_rate_limited_until: float = 0.0


class TapetideNotConfiguredError(RuntimeError):
    """Raised when TAPETIDE_TOKEN is missing."""


class TapetideRateLimitError(RuntimeError):
    """Raised when Tapetide free-tier or hourly quota is exhausted."""


def _token() -> str:
    token = os.getenv("TAPETIDE_TOKEN", "").strip() or os.getenv("TAPETIDE_API_TOKEN", "").strip()
    if not token:
        raise TapetideNotConfiguredError(
            "TAPETIDE_TOKEN is not set. Get a free token at https://tapetide.com/settings/tokens"
        )
    return token


def is_enabled() -> bool:
    """Tapetide is always enabled when a token is configured."""
    return True


def is_configured() -> bool:
    try:
        _token()
        return True
    except TapetideNotConfiguredError:
        return False


def is_rate_limited() -> bool:
    return time.monotonic() < _rate_limited_until


def is_active(*, batch: bool | None = None) -> bool:
    """Tapetide is attempted whenever TAPETIDE_TOKEN is set (failures handled per call)."""
    return is_configured()


def _mcp_url() -> str:
    return os.getenv("TAPETIDE_MCP_URL", DEFAULT_MCP_URL).rstrip("/")


def _disk_cache_dir() -> Path:
    from trade_integrations.context.hub import get_hub_dir

    return get_hub_dir() / "_data" / "tapetide_cache"


def _disk_cache_minutes() -> int:
    try:
        return max(0, int(os.getenv("TAPETIDE_CACHE_MINUTES", str(DISK_CACHE_DEFAULT_MINUTES))))
    except ValueError:
        return DISK_CACHE_DEFAULT_MINUTES


def _disk_cache_path(tool: str, symbol: str) -> Path:
    safe = symbol.strip().upper().replace("/", "_")
    return _disk_cache_dir() / f"{safe}_{tool}.json"


def _load_disk_cache(tool: str, symbol: str) -> dict[str, Any] | None:
    minutes = _disk_cache_minutes()
    if minutes <= 0:
        return None
    path = _disk_cache_path(tool, symbol)
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    saved_at = payload.get("saved_at")
    data = payload.get("data")
    if not isinstance(data, dict) or not saved_at:
        return None
    try:
        ts = datetime.fromisoformat(str(saved_at))
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        age_min = (datetime.now(timezone.utc) - ts).total_seconds() / 60.0
    except ValueError:
        return None
    if age_min > minutes:
        return None
    return data


def _save_disk_cache(tool: str, symbol: str, data: dict[str, Any]) -> None:
    minutes = _disk_cache_minutes()
    if minutes <= 0 or not data:
        return
    path = _disk_cache_path(tool, symbol)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {"saved_at": datetime.now(timezone.utc).isoformat(), "data": data},
                indent=2,
                default=str,
            ),
            encoding="utf-8",
        )
    except OSError as exc:
        logger.debug("Tapetide disk cache write failed for %s: %s", symbol, exc)


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


def call_tool(tool_name: str, arguments: dict[str, Any]) -> Any:
    """Invoke one Tapetide MCP tool and return parsed result content."""
    if is_rate_limited():
        raise TapetideRateLimitError("Tapetide calls paused after rate limit (retry in ~1 hour).")

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
    if response.status_code == 429:
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


def _cache_get(cache: dict[str, tuple[float, dict[str, Any]]], key: str) -> dict[str, Any] | None:
    row = cache.get(key)
    if not row:
        return None
    expires_at, payload = row
    if time.monotonic() > expires_at:
        cache.pop(key, None)
        return None
    return payload


def _cache_set(cache: dict[str, tuple[float, dict[str, Any]]], key: str, payload: dict[str, Any]) -> None:
    cache[key] = (time.monotonic() + PROFILE_CACHE_TTL_SEC, payload)


def get_company_profile(symbol: str, *, include_peers: bool = True) -> dict[str, Any]:
    symbol_upper = symbol.upper()
    # One MCP profile call per symbol (always include peers); callers ignore peers when unused.
    cache_key = f"{symbol_upper}:profile"
    cached = _cache_get(_profile_cache, cache_key)
    if cached is not None:
        return cached

    disk_cached = _load_disk_cache("profile_peers", symbol_upper)
    if disk_cached is not None:
        _cache_set(_profile_cache, cache_key, disk_cached)
        return disk_cached

    data = call_tool(
        "get_company_profile",
        {"symbol": symbol_upper, "include": ["peers"]},
    )
    result = data if isinstance(data, dict) else {"raw": data}
    if result.get("raw_text") and is_rate_limit_message(str(result["raw_text"])):
        _mark_rate_limited(str(result["raw_text"]))
        raise TapetideRateLimitError(str(result["raw_text"]))

    _cache_set(_profile_cache, cache_key, result)
    _save_disk_cache("profile_peers", symbol_upper, result)
    return result


def get_stock_events(symbol: str, *, limit: int = 20) -> dict[str, Any]:
    symbol_upper = symbol.upper()
    cache_key = f"{symbol_upper}:limit={limit}"
    cached = _cache_get(_events_cache, cache_key)
    if cached is not None:
        return cached

    disk_cached = _load_disk_cache("events", symbol_upper)
    if disk_cached is not None:
        _cache_set(_events_cache, cache_key, disk_cached)
        return disk_cached

    data = call_tool(
        "get_stock_events",
        {"symbol": symbol_upper, "limit": limit},
    )
    result = data if isinstance(data, dict) else {"raw": data}
    if result.get("raw_text") and is_rate_limit_message(str(result["raw_text"])):
        _mark_rate_limited(str(result["raw_text"]))
        raise TapetideRateLimitError(str(result["raw_text"]))

    _cache_set(_events_cache, cache_key, result)
    _save_disk_cache("events", symbol_upper, result)
    return result
