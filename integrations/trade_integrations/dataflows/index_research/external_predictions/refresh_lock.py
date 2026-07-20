"""Single-flight lock for external predictions refresh (SSE + sync)."""

from __future__ import annotations

import json
import os
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from trade_integrations.context.hub import get_hub_dir

_LOCK_NAME = "refresh.lock"
_STALE_SEC = 45 * 60


def _lock_path() -> Path:
    path = get_hub_dir() / "_data" / "external_predictions" / _LOCK_NAME
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _read_lock_meta() -> dict:
    path = _lock_path()
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def refresh_lock_status() -> dict[str, object]:
    meta = _read_lock_meta()
    pid = int(meta.get("pid") or 0)
    started = float(meta.get("started_at") or 0)
    alive = _pid_alive(pid)
    stale = bool(started) and (time.time() - started) > _STALE_SEC
    return {
        "locked": bool(meta) and alive and not stale,
        "pid": pid,
        "started_at": started,
        "holder_alive": alive,
        "stale": stale,
    }


@contextmanager
def external_refresh_lock(
    *,
    symbol: str = "NIFTY",
    horizon_days: int = 14,
) -> Iterator[None]:
    """Process-wide exclusive lock held for the whole refresh."""
    import fcntl

    path = _lock_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(path), os.O_CREAT | os.O_RDWR)
    acquired = False
    try:
        for attempt in range(2):
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                acquired = True
                break
            except BlockingIOError:
                meta = _read_lock_meta()
                pid = int(meta.get("pid") or 0)
                started = float(meta.get("started_at") or 0)
                stale = bool(started) and (time.time() - started) > _STALE_SEC
                if attempt == 0 and ((pid and not _pid_alive(pid)) or stale):
                    continue
                holder = meta.get("symbol") or symbol
                hz = meta.get("horizon_days") or horizon_days
                raise RuntimeError(
                    "External predictions refresh already running "
                    f"({holder} {hz}d, pid={pid}). Wait for it to finish before starting another."
                ) from None

        payload = {
            "pid": os.getpid(),
            "started_at": time.time(),
            "symbol": symbol.upper(),
            "horizon_days": horizon_days,
        }
        os.ftruncate(fd, 0)
        os.lseek(fd, 0, os.SEEK_SET)
        os.write(fd, json.dumps(payload).encode("utf-8"))
        yield
    finally:
        if acquired:
            import fcntl

            try:
                os.ftruncate(fd, 0)
            except OSError:
                pass
            fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def reset_external_refresh_lock_for_tests() -> None:
    path = _lock_path()
    try:
        if path.is_file():
            path.unlink()
    except OSError:
        pass
