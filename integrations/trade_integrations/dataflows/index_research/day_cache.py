"""Trading-day cache for index research — cache-first, live fetch on miss."""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, TypeVar

from trade_integrations.context.hub import get_hub_dir

logger = logging.getLogger(__name__)

T = TypeVar("T")

_CACHE_DIR_NAME = "_data/day_cache"
_NAMESPACE_RE = re.compile(r"^[a-z0-9][a-z0-9._:-]{0,127}$", re.I)


def _cache_root() -> Path:
    path = get_hub_dir() / _CACHE_DIR_NAME
    path.mkdir(parents=True, exist_ok=True)
    return path


def _normalize_namespace(namespace: str) -> str:
    key = (namespace or "").strip()
    if not key or not _NAMESPACE_RE.fullmatch(key):
        raise ValueError(f"invalid day_cache namespace: {namespace!r}")
    return key.replace(":", "_")


def _normalize_trading_day(trading_day: str) -> str:
    day = (trading_day or "")[:10]
    if len(day) != 10 or day[4] != "-" or day[7] != "-":
        raise ValueError(f"invalid trading_day: {trading_day!r}")
    return day


def cache_path(*, namespace: str, trading_day: str) -> Path:
    """Return on-disk path for a namespace + trading day entry."""
    ns = _normalize_namespace(namespace)
    day = _normalize_trading_day(trading_day)
    return _cache_root() / ns / f"{day}.json"


def read_cached(*, namespace: str, trading_day: str) -> dict[str, Any] | None:
    """Load a cached envelope or return None if missing/invalid."""
    path = cache_path(namespace=namespace, trading_day=trading_day)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.debug("day_cache read failed %s: %s", path, exc)
        return None
    if not isinstance(data, dict):
        return None
    if str(data.get("trading_day") or "")[:10] != _normalize_trading_day(trading_day):
        return None
    return data


def write_cached(*, namespace: str, trading_day: str, payload: Any) -> Path:
    """Persist payload under trading_day envelope."""
    path = cache_path(namespace=namespace, trading_day=trading_day)
    path.parent.mkdir(parents=True, exist_ok=True)
    envelope = {
        "trading_day": _normalize_trading_day(trading_day),
        "cached_at": datetime.now(timezone.utc).isoformat(),
        "payload": payload,
    }
    path.write_text(json.dumps(envelope, indent=2, default=str), encoding="utf-8")
    return path


def get_or_fetch(
    *,
    namespace: str,
    trading_day: str,
    fetch_fn: Callable[[], T],
    force: bool = False,
) -> tuple[T, bool]:
    """Return (payload, cached). On miss or force, call fetch_fn and store."""
    if not force:
        envelope = read_cached(namespace=namespace, trading_day=trading_day)
        if envelope is not None and "payload" in envelope:
            return envelope["payload"], True

    payload = fetch_fn()
    write_cached(namespace=namespace, trading_day=trading_day, payload=payload)
    return payload, False


def invalidate(*, namespace: str, trading_day: str) -> bool:
    """Remove a cached entry if present."""
    path = cache_path(namespace=namespace, trading_day=trading_day)
    if not path.is_file():
        return False
    try:
        path.unlink()
        return True
    except OSError as exc:
        logger.debug("day_cache invalidate failed %s: %s", path, exc)
        return False
