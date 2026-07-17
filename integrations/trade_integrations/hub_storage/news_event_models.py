"""Dataclasses for hub distilled news events."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class NewsReference:
    ref_id: str = ""
    url: str = ""
    publisher: str = ""
    vendor: str = ""
    raw_title: str = ""
    raw_summary: str = ""
    published_at: str = ""
    fetched_at: str = ""
    extracted_claims: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ref_id": self.ref_id,
            "url": self.url,
            "publisher": self.publisher,
            "vendor": self.vendor,
            "raw_title": self.raw_title,
            "raw_summary": self.raw_summary,
            "published_at": self.published_at,
            "fetched_at": self.fetched_at,
            "extracted_claims": list(self.extracted_claims),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> NewsReference:
        if not isinstance(data, dict):
            return cls()
        return cls(
            ref_id=str(data.get("ref_id") or ""),
            url=str(data.get("url") or ""),
            publisher=str(data.get("publisher") or ""),
            vendor=str(data.get("vendor") or ""),
            raw_title=str(data.get("raw_title") or ""),
            raw_summary=str(data.get("raw_summary") or ""),
            published_at=str(data.get("published_at") or ""),
            fetched_at=str(data.get("fetched_at") or ""),
            extracted_claims=list(data.get("extracted_claims") or []),
        )


@dataclass
class TimelineEntry:
    at: str = ""
    kind: str = "update"
    summary: str = ""
    source_ref_ids: list[str] = field(default_factory=list)
    consensus_snapshot: dict[str, Any] = field(default_factory=dict)
    publisher: str = ""
    raw_title: str = ""
    ref_urls: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "at": self.at,
            "kind": self.kind,
            "summary": self.summary,
        }
        if self.source_ref_ids:
            payload["source_ref_ids"] = list(self.source_ref_ids)
        if self.consensus_snapshot:
            payload["consensus_snapshot"] = dict(self.consensus_snapshot)
        if self.publisher:
            payload["publisher"] = self.publisher
        if self.raw_title:
            payload["raw_title"] = self.raw_title
        if self.ref_urls:
            payload["ref_urls"] = list(self.ref_urls)
        return payload

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> TimelineEntry:
        if not isinstance(data, dict):
            return cls()
        return cls(
            at=str(data.get("at") or ""),
            kind=str(data.get("kind") or "update"),
            summary=str(data.get("summary") or ""),
            source_ref_ids=list(data.get("source_ref_ids") or []),
            consensus_snapshot=dict(data.get("consensus_snapshot") or {}),
            publisher=str(data.get("publisher") or ""),
            raw_title=str(data.get("raw_title") or ""),
            ref_urls=list(data.get("ref_urls") or []),
        )


@dataclass
class EventConsensus:
    direction: str = "neutral"
    primary_factors: list[str] = field(default_factory=list)
    nifty_level_range: list[float] = field(default_factory=list)
    narrative: str = ""
    confidence: float = 0.0
    conflicts: list[dict[str, Any]] = field(default_factory=list)
    topics: list[str] = field(default_factory=list)
    factors: list[str] = field(default_factory=list)
    publishers: list[str] = field(default_factory=list)
    ref_count: int = 0
    publish_day: str = ""

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "direction": self.direction,
            "primary_factors": list(self.primary_factors),
            "confidence": self.confidence,
            "conflicts": list(self.conflicts),
        }
        if self.nifty_level_range:
            payload["nifty_level_range"] = list(self.nifty_level_range)
        if self.narrative:
            payload["narrative"] = self.narrative
        if self.topics:
            payload["topics"] = list(self.topics)
        if self.factors:
            payload["factors"] = list(self.factors)
        if self.publishers:
            payload["publishers"] = list(self.publishers)
        if self.ref_count:
            payload["ref_count"] = self.ref_count
        if self.publish_day:
            payload["publish_day"] = self.publish_day
        return payload

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> EventConsensus:
        if not isinstance(data, dict):
            return cls()
        level_range = data.get("nifty_level_range") or []
        return cls(
            direction=str(data.get("direction") or "neutral"),
            primary_factors=list(data.get("primary_factors") or data.get("factors") or []),
            nifty_level_range=[float(x) for x in level_range if x is not None],
            narrative=str(data.get("narrative") or ""),
            confidence=float(data.get("confidence") or 0.0),
            conflicts=list(data.get("conflicts") or []),
            topics=list(data.get("topics") or []),
            factors=list(data.get("factors") or []),
            publishers=list(data.get("publishers") or []),
            ref_count=int(data.get("ref_count") or 0),
            publish_day=str(data.get("publish_day") or ""),
        )


@dataclass
class DistilledNewsEvent:
    event_id: str
    ticker: str
    title: str
    content: str
    publish_day: str = ""
    timeline: list[TimelineEntry] = field(default_factory=list)
    references: list[NewsReference] = field(default_factory=list)
    consensus: EventConsensus = field(default_factory=EventConsensus)
    tags: dict[str, Any] = field(default_factory=dict)
    predicted_impact: dict[str, Any] = field(default_factory=dict)
    actual_impact: dict[str, Any] = field(default_factory=dict)
    status: str = "active"
    processing_version: int = 1
    first_seen_at: str = ""
    updated_at: str = ""
    structured_summary: dict[str, Any] = field(default_factory=dict)
    verification_status: str = "pending"
    sources: list[dict[str, Any]] = field(default_factory=list)
    published_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "ticker": self.ticker,
            "title": self.title,
            "content": self.content,
            "publish_day": self.publish_day,
            "timeline": [entry.to_dict() for entry in self.timeline],
            "references": [ref.to_dict() for ref in self.references],
            "consensus": self.consensus.to_dict(),
            "tags": dict(self.tags),
            "predicted_impact": dict(self.predicted_impact),
            "actual_impact": dict(self.actual_impact),
            "status": self.status,
            "processing_version": self.processing_version,
            "first_seen_at": self.first_seen_at,
            "updated_at": self.updated_at,
            "structured_summary": dict(self.structured_summary),
            "verification_status": self.verification_status,
            "sources": list(self.sources),
            "published_at": self.published_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> DistilledNewsEvent:
        timeline = [TimelineEntry.from_dict(row) for row in (data.get("timeline") or [])]
        references = [NewsReference.from_dict(row) for row in (data.get("references") or [])]
        return cls(
            event_id=str(data.get("event_id") or ""),
            ticker=str(data.get("ticker") or "NIFTY"),
            title=str(data.get("title") or ""),
            content=str(data.get("content") or ""),
            publish_day=str(data.get("publish_day") or ""),
            timeline=timeline,
            references=references,
            consensus=EventConsensus.from_dict(data.get("consensus")),
            tags=dict(data.get("tags") or {}),
            predicted_impact=dict(data.get("predicted_impact") or {}),
            actual_impact=dict(data.get("actual_impact") or {}),
            status=str(data.get("status") or "active"),
            processing_version=int(data.get("processing_version") or 1),
            first_seen_at=str(data.get("first_seen_at") or ""),
            updated_at=str(data.get("updated_at") or ""),
            structured_summary=dict(data.get("structured_summary") or {}),
            verification_status=str(data.get("verification_status") or "pending"),
            sources=list(data.get("sources") or []),
            published_at=str(data.get("published_at") or ""),
        )
