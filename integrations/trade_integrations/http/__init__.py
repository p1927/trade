"""Outbound HTTP gateway — sole entry point for trade-stack HTTP from integrations.

Use::

    from trade_integrations.http import get, post, PoolKind, nse_session

Do not import ``requests`` directly in ``trade_integrations/`` call sites.
"""

from __future__ import annotations

from trade_integrations.http.gateway import (
    DEFAULT_TIMEOUT,
    DEFAULT_USER_AGENT,
    HTTPError,
    PoolKind,
    RequestException,
    Response,
    cookie_jar,
    get,
    get_session,
    nse_session,
    post,
    scoped_session,
)
from trade_integrations.http.lifecycle import close_http_gateway, init_http_gateway

__all__ = [
    "DEFAULT_TIMEOUT",
    "DEFAULT_USER_AGENT",
    "HTTPError",
    "PoolKind",
    "RequestException",
    "Response",
    "close_http_gateway",
    "cookie_jar",
    "get",
    "get_session",
    "init_http_gateway",
    "nse_session",
    "post",
    "scoped_session",
]
