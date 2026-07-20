"""Sole entry point for SearXNG ``/search`` — all callers must use this module.

Do::

    from trade_integrations.dataflows import searxng_client
    payload = searxng_client.search_json("NIFTY stock news", categories="news")

Don't::

    requests.get(SEARXNG_BASE_URL + "/search", ...)
"""

from __future__ import annotations

import json
import logging
import os
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

import requests

from trade_integrations.context.hub import get_hub_dir

logger = logging.getLogger(__name__)

_DATA_DIR = Path("_data") / "searxng"
_DRAIN_LOCK = "drain.lock"
_LAST_CALL = "last_call.json"
_WAITING = "waiting.json"
_DEFAULT_MIN_INTERVAL = 0.5
_DEFAULT_TIMEOUT = 30.0


def _searxng_data_dir() -> Path:
    path = get_hub_dir() / _DATA_DIR
    path.mkdir(parents=True, exist_ok=True)
    return path


def _lock_path() -> Path:
    return _searxng_data_dir() / _DRAIN_LOCK


def _last_call_path() -> Path:
    return _searxng_data_dir() / _LAST_CALL


def _waiting_path() -> Path:
    return _searxng_data_dir() / _WAITING


def _min_interval_sec() -> float:
    raw = os.environ.get("SEARXNG_MIN_INTERVAL_SEC", str(_DEFAULT_MIN_INTERVAL)).strip()
    try:
        return max(0.0, float(raw))
    except ValueError:
        return _DEFAULT_MIN_INTERVAL


def _base_url() -> str:
    from trade_integrations.stack_ports import searxng_base_url

    return os.environ.get("SEARXNG_BASE_URL", searxng_base_url()).rstrip("/")


class _CounterLock:
    """Short-lived flock for updating waiting metadata without holding the drain slot."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._fd: int | None = None

    def __enter__(self) -> _CounterLock:
        import fcntl

        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._fd = os.open(str(self._path), os.O_CREAT | os.O_RDWR)
        fcntl.flock(self._fd, fcntl.LOCK_EX)
        return self

    def __exit__(self, *args: object) -> None:
        import fcntl

        if self._fd is not None:
            fcntl.flock(self._fd, fcntl.LOCK_UN)
            os.close(self._fd)
            self._fd = None


class _DrainLock:
    """Cross-process exclusive lock for the SearXNG drain slot."""

    def __init__(self) -> None:
        self._path = _lock_path()
        self._fd: int | None = None

    def __enter__(self) -> _DrainLock:
        import fcntl

        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._fd = os.open(str(self._path), os.O_CREAT | os.O_RDWR)
        fcntl.flock(self._fd, fcntl.LOCK_EX)
        return self

    def __exit__(self, *args: object) -> None:
        import fcntl

        if self._fd is not None:
            fcntl.flock(self._fd, fcntl.LOCK_UN)
            os.close(self._fd)
            self._fd = None


def _read_last_call_epoch() -> float:
    path = _last_call_path()
    if not path.is_file():
        return 0.0
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return float(data.get("epoch") or 0.0)
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return 0.0


def _write_last_call_epoch(epoch: float) -> None:
    path = _last_call_path()
    path.write_text(json.dumps({"epoch": epoch}), encoding="utf-8")


def _read_waiting() -> int:
    path = _waiting_path()
    if not path.is_file():
        return 0
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return max(0, int(data.get("waiting") or 0))
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return 0


def _write_waiting(count: int) -> None:
    path = _waiting_path()
    path.write_text(json.dumps({"waiting": max(0, count)}), encoding="utf-8")


def _waiting_lock_path() -> Path:
    return _searxng_data_dir() / "waiting.lock"


def _adjust_waiting(delta: int) -> None:
    with _CounterLock(_waiting_lock_path()):
        _write_waiting(_read_waiting() + delta)


@contextmanager
def _global_drain_slot():
    _adjust_waiting(1)
    try:
        with _DrainLock():
            spacing = _min_interval_sec()
            if spacing > 0:
                last = _read_last_call_epoch()
                if last > 0:
                    wait = spacing - (time.time() - last)
                    if wait > 0:
                        time.sleep(wait)
            try:
                yield
            finally:
                _write_last_call_epoch(time.time())
    finally:
        _adjust_waiting(-1)


def search_json(
    q: str,
    *,
    categories: str = "news",
    timeout: float = _DEFAULT_TIMEOUT,
) -> dict[str, Any]:
    """Queued SearXNG JSON search — blocks until the global drain slot is available."""
    from trade_integrations.dataflows import source_availability

    if not source_availability.should_attempt("searxng", "search"):
        return {"results": []}

    with _global_drain_slot():
        url = urljoin(_base_url() + "/", "search")
        params: dict[str, str] = {"q": q, "format": "json"}
        if categories:
            params["categories"] = categories
        try:
            resp = requests.get(url, params=params, timeout=timeout)
            resp.raise_for_status()
        except requests.RequestException as exc:
            source_availability.record_failure("searxng", "search", exc)
            raise
        source_availability.record_success("searxng", "search")
        return resp.json()


def searxng_queue_stats() -> dict[str, Any]:
    """Lightweight queue / spacing observability for ops and UI."""
    return {
        "min_interval_sec": _min_interval_sec(),
        "last_call_epoch": _read_last_call_epoch(),
        "waiting": _read_waiting(),
    }


def reset_searxng_client_for_tests() -> None:
    """Clear hub-side queue metadata (tests only)."""
    for path in (_last_call_path(), _waiting_path()):
        try:
            if path.is_file():
                path.unlink()
        except OSError:
            logger.debug("could not remove %s during test reset", path, exc_info=True)
