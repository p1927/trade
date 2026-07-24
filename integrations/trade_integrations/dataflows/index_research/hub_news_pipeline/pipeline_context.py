"""Shared context passed between hub news pipeline steps."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class StepResult:
    step_id: str
    status: str  # ok | skipped | failed | discarded
    duration_ms: float = 0.0
    error: str = ""
    detail: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "step_id": self.step_id,
            "status": self.status,
            "duration_ms": self.duration_ms,
            "error": self.error,
            "detail": dict(self.detail),
        }


@dataclass
class RefPipelineContext:
    """Mutable state for one staging ref flowing through pipeline steps."""

    ref: dict[str, Any]
    ticker: str = "NIFTY"
    should_continue: bool = True
    discard_reason: str = ""
    relevance_verdict: dict[str, Any] = field(default_factory=dict)
    enrichment_mode: str = ""  # full | snippet_fallback
    fetch_status: str = ""
    fetch_method: str = ""
    article_body: str = ""
    published_at: str = ""
    publish_day: str = ""
    date_conflict: bool = False
    timezone_source: str = ""
    article_enrichment: dict[str, Any] = field(default_factory=dict)
    step_trace: list[StepResult] = field(default_factory=list)

    def record_step(self, result: StepResult) -> None:
        self.step_trace.append(result)

    def trace_dicts(self) -> list[dict[str, Any]]:
        return [row.to_dict() for row in self.step_trace]
