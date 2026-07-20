"""Unified fetch types for DataRouter."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal


FetchStatus = Literal["ok", "partial", "miss"]
FetchMode = Literal["sequential", "parallel_merge", "parallel_dedupe"]
SourceTier = Literal["free", "tiered", "capture", "mission"]


@dataclass
class SourceAttempt:
    name: str
    status: str  # ok | error | skipped | budget_exhausted | no_data
    error: str = ""
    remediation: str = ""
    has_data: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "status": self.status,
            "error": self.error,
            "remediation": self.remediation,
            "has_data": self.has_data,
        }


@dataclass
class FetchSpec:
    domain: str
    market: str
    symbol: str | None = None
    start: str | None = None
    end: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    def spec_hash(self) -> str:
        import hashlib
        import json

        payload = {
            "domain": self.domain,
            "market": self.market,
            "symbol": (self.symbol or "").upper(),
            "start": self.start,
            "end": self.end,
            "extra": self.extra,
        }
        digest = hashlib.sha256(
            json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
        ).hexdigest()
        return digest[:16]


@dataclass
class FetchResult:
    status: FetchStatus
    data: Any = None
    source_id: str | None = None
    attempts: list[SourceAttempt] = field(default_factory=list)
    normalized_path: str | None = None
    pending_job_id: str | None = None
    cache_hit: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "source_id": self.source_id,
            "attempts": [a.to_dict() for a in self.attempts],
            "normalized_path": self.normalized_path,
            "pending_job_id": self.pending_job_id,
            "cache_hit": self.cache_hit,
        }
