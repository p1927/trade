"""Rate-limited HTTP fetch with retries for external dataset ingest."""

from __future__ import annotations

import logging
import os
import time
from pathlib import Path

import requests

logger = logging.getLogger(__name__)

_UA = "trade-stack-research/0.1 (+https://github.com/p1927/trade)"
_LAST_FETCH_AT = 0.0

_RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504})


def fetch_delay_sec() -> float:
    return float(os.environ.get("TRADE_FETCH_DELAY_SEC", "1.5"))


def max_fetch_retries() -> int:
    return int(os.environ.get("TRADE_FETCH_MAX_RETRIES", "5"))


def _pace(min_delay: float) -> None:
    global _LAST_FETCH_AT
    now = time.monotonic()
    wait = min_delay - (now - _LAST_FETCH_AT)
    if wait > 0:
        time.sleep(wait)
    _LAST_FETCH_AT = time.monotonic()


def fetch_bytes(
    url: str,
    *,
    timeout: float = 180,
    min_delay: float | None = None,
    max_retries: int | None = None,
) -> bytes:
    """GET url with inter-request pacing and exponential backoff on transient failures."""
    delay = fetch_delay_sec() if min_delay is None else min_delay
    retries = max_fetch_retries() if max_retries is None else max_retries
    last_exc: Exception | None = None

    for attempt in range(retries):
        _pace(delay)
        try:
            resp = requests.get(url, headers={"User-Agent": _UA}, timeout=timeout)
            if resp.status_code in _RETRYABLE_STATUS:
                raise requests.HTTPError(f"{resp.status_code} for {url}", response=resp)
            resp.raise_for_status()
            return resp.content
        except requests.HTTPError as exc:
            last_exc = exc
            status = exc.response.status_code if exc.response is not None else None
            if status is not None and 400 <= status < 500 and status != 429:
                raise
            if attempt + 1 >= retries:
                break
            backoff = delay * (2**attempt)
            if status == 429:
                backoff = max(backoff, 30.0)
            logger.warning(
                "Fetch attempt %s/%s failed for %s: %s — retry in %.1fs",
                attempt + 1,
                retries,
                url,
                exc,
                backoff,
            )
            time.sleep(backoff)
        except (requests.Timeout, requests.ConnectionError) as exc:
            last_exc = exc
            if attempt + 1 >= retries:
                break
            backoff = delay * (2**attempt)
            logger.warning(
                "Fetch attempt %s/%s failed for %s: %s — retry in %.1fs",
                attempt + 1,
                retries,
                url,
                exc,
                backoff,
            )
            time.sleep(backoff)

    raise RuntimeError(f"Failed to fetch {url} after {retries} attempts") from last_exc


def fetch_to_path(
    url: str,
    dest: Path,
    *,
    force: bool = False,
    timeout: float = 180,
    min_delay: float | None = None,
    max_retries: int | None = None,
) -> Path:
    if dest.is_file() and not force:
        return dest
    logger.info("Fetching %s", url)
    body = fetch_bytes(url, timeout=timeout, min_delay=min_delay, max_retries=max_retries)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(body)
    return dest
