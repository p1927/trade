"""Structured pipeline activity log for NIFTY index research."""

from __future__ import annotations

import time
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Iterator

LogCallback = Callable[["PipelineLogEntry"], None]


@dataclass
class PipelineLogEntry:
    """One timestamped pipeline activity line."""

    stage: str
    message: str
    level: str = "info"
    detail: dict[str, Any] = field(default_factory=dict)
    at: str = ""

    def __post_init__(self) -> None:
        if not self.at:
            self.at = datetime.now(timezone.utc).isoformat()

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class PipelineLogger:
    """Collects log entries and optionally forwards them to a live callback."""

    def __init__(self, on_entry: LogCallback | None = None) -> None:
        self._entries: list[PipelineLogEntry] = []
        self._on_entry = on_entry

    @property
    def entries(self) -> list[PipelineLogEntry]:
        return list(self._entries)

    def log(
        self,
        stage: str,
        message: str,
        *,
        level: str = "info",
        **detail: Any,
    ) -> None:
        entry = PipelineLogEntry(stage=stage, message=message, level=level, detail=detail)
        self._entries.append(entry)
        if self._on_entry is not None:
            self._on_entry(entry)

    def info(self, stage: str, message: str, **detail: Any) -> None:
        self.log(stage, message, level="info", **detail)

    def warn(self, stage: str, message: str, **detail: Any) -> None:
        self.log(stage, message, level="warn", **detail)

    def error(self, stage: str, message: str, **detail: Any) -> None:
        self.log(stage, message, level="error", **detail)

    @contextmanager
    def stage_timer(self, stage: str, message: str, **detail: Any) -> Iterator[None]:
        """Log start message and append elapsed_ms on exit."""
        start = time.perf_counter()
        self.info(stage, message, **detail)
        try:
            yield
        finally:
            elapsed_ms = round((time.perf_counter() - start) * 1000, 1)
            self.info(stage, message, elapsed_ms=elapsed_ms, **detail)

    def to_dicts(self) -> list[dict[str, Any]]:
        return [entry.to_dict() for entry in self._entries]
