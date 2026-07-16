"""JSON-safe serialization helpers for MCP tool payloads."""

from __future__ import annotations

from typing import Any


def json_safe(value: Any, *, _seen: set[int] | None = None) -> Any:
    """Recursively convert values to JSON-serializable primitives; break cycles."""
    if _seen is None:
        _seen = set()

    if value is None or isinstance(value, (str, int, float, bool)):
        return value

    oid = id(value)
    if oid in _seen:
        return "<circular>"

    if hasattr(value, "item") and callable(value.item):
        try:
            return json_safe(value.item(), _seen=_seen)
        except Exception:
            pass

    if hasattr(value, "model_dump") and callable(value.model_dump):
        _seen.add(oid)
        try:
            return json_safe(
                value.model_dump(mode="json", by_alias=True, exclude_none=True),
                _seen=_seen,
            )
        finally:
            _seen.discard(oid)

    if isinstance(value, dict):
        _seen.add(oid)
        try:
            return {str(k): json_safe(v, _seen=_seen) for k, v in value.items()}
        finally:
            _seen.discard(oid)

    if isinstance(value, (list, tuple, set)):
        _seen.add(oid)
        try:
            return [json_safe(v, _seen=_seen) for v in value]
        finally:
            _seen.discard(oid)

    return str(value)
