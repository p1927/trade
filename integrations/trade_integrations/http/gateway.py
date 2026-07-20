"""Process-wide HTTP connection pools with explicit lifecycle."""

from __future__ import annotations

import threading
from contextlib import contextmanager
from enum import Enum
from typing import Any, Iterator

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# Re-export for callers — only this module imports requests directly.
RequestException = requests.RequestException
HTTPError = requests.HTTPError
Response = requests.Response

DEFAULT_USER_AGENT = "trade-stack-research/0.1 (+https://github.com/p1927/trade)"
DEFAULT_TIMEOUT = 45.0

_NSE_HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Accept": "*/*",
    "Referer": "https://www.nseindia.com/reports/fii-dii",
}

_lock = threading.Lock()
_sessions: dict[str, requests.Session | None] = {
    "general": None,
    "openalgo": None,
}


class PoolKind(str, Enum):
    GENERAL = "general"
    OPENALGO = "openalgo"


def _build_session(*, user_agent: str | None = None) -> requests.Session:
    session = requests.Session()
    retry = Retry(total=0)
    adapter = HTTPAdapter(
        pool_connections=10,
        pool_maxsize=20,
        pool_block=True,
        max_retries=retry,
    )
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    session.headers["User-Agent"] = user_agent or DEFAULT_USER_AGENT
    return session


def _pool_key(pool: PoolKind) -> str:
    return pool.value


def get_session(*, pool: PoolKind = PoolKind.GENERAL) -> requests.Session:
    """Return a process-wide pooled session (thread-safe lazy init)."""
    key = _pool_key(pool)
    with _lock:
        session = _sessions.get(key)
        if session is None:
            session = _build_session()
            _sessions[key] = session
        return session


def get(
    url: str,
    *,
    pool: PoolKind = PoolKind.GENERAL,
    timeout: float = DEFAULT_TIMEOUT,
    **kwargs: Any,
) -> requests.Response:
    kwargs.setdefault("timeout", timeout)
    return get_session(pool=pool).get(url, **kwargs)


def post(
    url: str,
    *,
    pool: PoolKind = PoolKind.GENERAL,
    timeout: float = DEFAULT_TIMEOUT,
    **kwargs: Any,
) -> requests.Response:
    kwargs.setdefault("timeout", timeout)
    return get_session(pool=pool).post(url, **kwargs)


def cookie_jar() -> requests.cookies.RequestsCookieJar:
    """Build an empty cookie jar for scoped sessions."""
    return requests.cookies.RequestsCookieJar()


@contextmanager
def scoped_session(
    *,
    headers: dict[str, str] | None = None,
    bootstrap_url: str | None = None,
    bootstrap_timeout: float = 15.0,
    user_agent: str | None = None,
) -> Iterator[requests.Session]:
    """Cookie/bootstrap session — always closed on exit."""
    session = _build_session(user_agent=user_agent)
    if headers:
        session.headers.update(headers)
    try:
        if bootstrap_url:
            session.get(bootstrap_url, timeout=bootstrap_timeout)
        yield session
    finally:
        session.close()


@contextmanager
def nse_session(*, bootstrap: bool = True) -> Iterator[requests.Session]:
    """NSE India session with optional homepage bootstrap."""
    with scoped_session(
        headers=dict(_NSE_HEADERS),
        bootstrap_url="https://www.nseindia.com" if bootstrap else None,
    ) as session:
        yield session


def _close_pool(key: str) -> None:
    with _lock:
        session = _sessions.get(key)
        if session is not None:
            try:
                session.close()
            except Exception:
                pass
        _sessions[key] = None


def close_all_pools() -> None:
    """Close every pooled session (idempotent)."""
    for key in list(_sessions.keys()):
        _close_pool(key)
