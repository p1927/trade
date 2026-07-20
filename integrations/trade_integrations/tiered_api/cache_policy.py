"""Decide whether vendor responses should be cached."""

from __future__ import annotations

from typing import Any


def should_cache_response(data: Any) -> bool:
    """Return False for empty payloads and known vendor error envelopes."""
    if data is None:
        return False
    if isinstance(data, str):
        text = data.strip()
        if not text:
            return False
        if text.startswith("{") or text.startswith("["):
            try:
                import json

                parsed = json.loads(text)
                return should_cache_response(parsed)
            except json.JSONDecodeError:
                return True
        return True
    if isinstance(data, (list, tuple)):
        return len(data) > 0
    if isinstance(data, dict):
        if not data:
            return False
        for key in ("Error Message", "Note", "Information", "error", "errors"):
            if key in data and data.get(key):
                msg = str(data[key]).lower()
                if any(
                    m in msg
                    for m in (
                        "rate limit",
                        "invalid api",
                        "api key",
                        "apikey",
                        "quota",
                        "not configured",
                    )
                ):
                    return False
        return True
    return True
