"""Watch feed port — WebSocket or in-memory tick delivery for Nautilus watch."""

from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class WatchTick:
    symbol: str
    ltp: float
    ts: str
    context_generation: str


class WatchFeedHandle(Protocol):
    """Subscribe to live ticks and poll accumulated updates."""

    def subscribe(self, symbols: list[str]) -> None: ...

    def poll_ticks(self) -> list[WatchTick]: ...

    def close(self) -> None: ...


class InMemoryWatchFeed:
    """Test double implementing :class:`WatchFeedHandle`."""

    def __init__(self, *, context_generation: str = "") -> None:
        self._context_generation = context_generation
        self._symbols: list[str] = []
        self._pending: list[WatchTick] = []
        self._lock = threading.Lock()
        self._closed = False

    def subscribe(self, symbols: list[str]) -> None:
        with self._lock:
            if self._closed:
                return
            self._symbols = [s.strip().upper() for s in symbols if s and s.strip()]

    def push_tick(self, symbol: str, ltp: float, *, ts: str) -> None:
        """Inject a tick (tests only)."""
        with self._lock:
            if self._closed:
                return
            self._pending.append(
                WatchTick(
                    symbol=symbol.strip().upper(),
                    ltp=float(ltp),
                    ts=ts,
                    context_generation=self._context_generation,
                )
            )

    def poll_ticks(self) -> list[WatchTick]:
        with self._lock:
            if self._closed:
                return []
            out = list(self._pending)
            self._pending.clear()
            return out

    def close(self) -> None:
        with self._lock:
            self._closed = True
            self._pending.clear()
