"""Normalized models for the company research pipeline."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal

StageStatus = Literal["ok", "partial", "skipped", "error"]


@dataclass
class StageResult:
    """Outcome of a single enrichment stage."""

    stage: str
    status: StageStatus
    vendor: str
    fetched_at: datetime
    data: dict[str, Any] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)


@dataclass
class CompanyResearchDoc:
    """Structured dossier built incrementally by the research pipeline."""

    ticker: str
    as_of: datetime
    lookahead_days: int
    market: str = ""
    identity: dict[str, Any] = field(default_factory=dict)
    peers: list[dict[str, Any]] = field(default_factory=list)
    calendar_events: list[dict[str, Any]] = field(default_factory=list)
    fundamentals: dict[str, Any] = field(default_factory=dict)
    filings: dict[str, Any] = field(default_factory=dict)
    news: dict[str, Any] = field(default_factory=dict)
    sentiment: dict[str, Any] = field(default_factory=dict)
    corp_events: dict[str, Any] = field(default_factory=dict)
    earnings_signal: dict[str, Any] = field(default_factory=dict)
    macro: dict[str, Any] = field(default_factory=dict)
    stages: list[StageResult] = field(default_factory=list)
