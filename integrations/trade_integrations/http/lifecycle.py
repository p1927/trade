"""Application lifecycle hooks for the outbound HTTP gateway."""

from __future__ import annotations

import logging

from trade_integrations.http.gateway import PoolKind, close_all_pools, get_session

logger = logging.getLogger(__name__)

_initialized = False


def init_http_gateway() -> None:
    """Warm general pool at process startup (optional but avoids first-request lock)."""
    global _initialized
    if _initialized:
        return
    get_session(pool=PoolKind.GENERAL)
    _initialized = True
    logger.debug("Outbound HTTP gateway initialized")


def close_http_gateway() -> None:
    """Drain connection pools on shutdown — do not rely on GC finalizers."""
    global _initialized
    close_all_pools()
    _initialized = False
    logger.debug("Outbound HTTP gateway closed")
