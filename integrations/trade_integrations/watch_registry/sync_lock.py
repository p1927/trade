"""Process-wide lock for watch registry mutations and Nautilus sync restarts."""

from __future__ import annotations

import os
import threading
from contextlib import contextmanager
from typing import Iterator

_thread_lock = threading.RLock()
_local = threading.local()


def _lock_path():
    from trade_integrations.autonomous_agents.nautilus_watch import _log_dir

    path = _log_dir() / "nautilus-watch.sync.lock"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _lock_depth() -> int:
    return int(getattr(_local, "depth", 0) or 0)


@contextmanager
def watch_registry_mutation_lock() -> Iterator[None]:
    """Exclusive lock for watch CRUD + registry sync (prevents duplicate Nautilus launches)."""
    import fcntl

    _thread_lock.acquire()
    depth = _lock_depth()
    opened_here = False
    fd: int | None = None
    try:
        if depth == 0:
            path = _lock_path()
            fd = os.open(str(path), os.O_CREAT | os.O_RDWR)
            fcntl.flock(fd, fcntl.LOCK_EX)
            _local.flock_fd = fd
            opened_here = True
        _local.depth = depth + 1
        yield
    finally:
        _local.depth = depth
        if opened_here and fd is not None:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)
            _local.flock_fd = None
        _thread_lock.release()


def reset_watch_registry_sync_lock_for_tests() -> None:
    try:
        path = _lock_path()
        if path.is_file():
            path.unlink()
    except OSError:
        pass
