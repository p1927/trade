"""Serialized MiniMax chat queue with rate-limit backoff for Token Plan usage."""

from __future__ import annotations

import logging
import os
import threading
import time
from typing import Any

logger = logging.getLogger(__name__)

_request_lock = threading.Lock()
_last_request_at = 0.0


def _spacing_seconds() -> float:
    try:
        return max(0.0, float(os.getenv("MINIMAX_QUEUE_SPACING_S", "0.75")))
    except ValueError:
        return 0.75


def _base_wait_seconds() -> float:
    try:
        return max(1.0, float(os.getenv("MINIMAX_RATE_LIMIT_BASE_WAIT_S", "60")))
    except ValueError:
        return 60.0


def _max_wait_seconds() -> float:
    try:
        return max(1.0, float(os.getenv("MINIMAX_RATE_LIMIT_MAX_WAIT_S", "300")))
    except ValueError:
        return 300.0


def _max_retries() -> int:
    try:
        return max(1, int(os.getenv("MINIMAX_RATE_LIMIT_MAX_RETRIES", "30")))
    except ValueError:
        return 30


def is_rate_limit_error(exc: BaseException) -> bool:
    """True when MiniMax rejected the call due to throttling."""
    if getattr(exc, "status_code", None) == 429:
        return True
    message = str(exc).lower()
    markers = (
        "429",
        "rate limit",
        "rate_limit",
        "rate_limit_error",
        "token plan rate limit",
        "usage limit exceeded",
        "too many requests",
    )
    return any(marker in message for marker in markers)


def _retry_wait_seconds(exc: BaseException, attempt: int) -> float:
    response = getattr(exc, "response", None)
    headers = getattr(response, "headers", None) if response is not None else None
    if headers:
        retry_after = headers.get("retry-after") or headers.get("Retry-After")
        if retry_after is not None:
            try:
                return min(float(retry_after), _max_wait_seconds())
            except (TypeError, ValueError):
                pass

    wait = _base_wait_seconds() * (1.5 ** min(attempt, 8))
    return min(wait, _max_wait_seconds())


def _wait_for_spacing() -> None:
    global _last_request_at
    spacing = _spacing_seconds()
    if spacing <= 0:
        return
    elapsed = time.monotonic() - _last_request_at
    if elapsed < spacing:
        time.sleep(spacing - elapsed)


def chat_completions_create(client: Any, **kwargs: Any) -> Any:
    """Run one MiniMax chat completion through the global serialized queue."""
    global _last_request_at
    retries = _max_retries()
    with _request_lock:
        for attempt in range(retries + 1):
            _wait_for_spacing()
            try:
                response = client.chat.completions.create(**kwargs)
                _last_request_at = time.monotonic()
                return response
            except Exception as exc:
                if not is_rate_limit_error(exc) or attempt >= retries:
                    raise
                wait_s = _retry_wait_seconds(exc, attempt)
                logger.warning(
                    "MiniMax rate limited (attempt %s/%s); waiting %.0fs before retry: %s",
                    attempt + 1,
                    retries + 1,
                    wait_s,
                    exc,
                )
                time.sleep(wait_s)
    raise RuntimeError("MiniMax chat queue exhausted retries")
