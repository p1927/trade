"""Redis lock for intent queue read-modify-write."""

from __future__ import annotations

import logging
import time
import uuid
from contextlib import contextmanager
from typing import Iterator

logger = logging.getLogger(__name__)

_LOCK_KEY = "nautilus:intent_queue:lock"
_LOCK_TTL_SEC = 30


def _redis_client():
    try:
        from nautilus_openalgo_bridge.config import get_bridge_config

        url = (get_bridge_config().redis_url or "").strip()
        if not url:
            return None
        import redis

        return redis.from_url(url, decode_responses=True)
    except Exception:
        return None


@contextmanager
def intent_queue_lock(*, wait_sec: float = 5.0) -> Iterator[bool]:
    """Acquire Redis lock for intent queue processing; yield False if unavailable."""
    client = _redis_client()
    if client is None:
        yield True
        return
    token = uuid.uuid4().hex
    deadline = time.monotonic() + max(0.1, wait_sec)
    acquired = False
    try:
        while time.monotonic() < deadline:
            if client.set(_LOCK_KEY, token, nx=True, ex=_LOCK_TTL_SEC):
                acquired = True
                break
            time.sleep(0.05)
        yield acquired
    finally:
        if acquired:
            try:
                if client.get(_LOCK_KEY) == token:
                    client.delete(_LOCK_KEY)
            except Exception:
                logger.debug("intent queue lock release failed", exc_info=True)
