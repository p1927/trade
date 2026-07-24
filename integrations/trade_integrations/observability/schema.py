"""Normalized observability event and issue shapes."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Literal

ObservabilityModule = Literal["watch", "llm", "ingest", "schedule", "pipeline", "hub", "system"]
ObservabilityLevel = Literal["info", "warn", "error"]
IssueSeverity = Literal["warn", "error"]
IssueStatus = Literal["open", "resolved"]


@dataclass
class ObservabilityEvent:
    module: ObservabilityModule
    event: str
    level: ObservabilityLevel = "info"
    trace_id: str = ""
    agent_id: str = ""
    session_id: str = ""
    job_id: str = ""
    ticker: str = ""
    duration_ms: int | None = None
    detail: dict[str, Any] = field(default_factory=dict)
    ts: str = ""

    def __post_init__(self) -> None:
        if not self.ts:
            self.ts = datetime.now(timezone.utc).isoformat()

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ObservabilityIssue:
    issue_id: str
    severity: IssueSeverity
    module: ObservabilityModule
    event: str
    status: IssueStatus = "open"
    first_seen: str = ""
    last_seen: str = ""
    count: int = 1
    summary: str = ""
    detail: dict[str, Any] = field(default_factory=dict)
    suggested_action: str = ""

    def __post_init__(self) -> None:
        now = datetime.now(timezone.utc).isoformat()
        if not self.first_seen:
            self.first_seen = now
        if not self.last_seen:
            self.last_seen = now

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
