"""Normalized article model shared across news aggregator sources."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass(frozen=True)
class SourceAttribution:
    """A publisher name tied to the backend that supplied it."""

    publisher: str
    vendor: str


@dataclass
class NewsArticle:
    """A single news item normalized from any backend."""

    title: str
    summary: str = ""
    link: str = ""
    source: str = ""
    vendor: str = ""
    pub_date: datetime | None = None
    vendors: list[str] = field(default_factory=list)
    attributions: list[SourceAttribution] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.title = (self.title or "").strip()
        self.summary = (self.summary or "").strip()
        self.link = (self.link or "").strip()
        self.source = (self.source or "").strip()
        if self.vendor and self.vendor not in self.vendors:
            self.vendors.append(self.vendor)
        if self.source or self.vendor:
            self._record_attribution(self.source, self.vendor)

    def _record_attribution(self, publisher: str, vendor: str) -> None:
        if not vendor:
            return
        labels = [part.strip() for part in publisher.split(",") if part.strip()]
        if not labels:
            labels = [vendor]
        for label in labels:
            entry = SourceAttribution(publisher=label, vendor=vendor)
            if entry not in self.attributions:
                self.attributions.append(entry)
