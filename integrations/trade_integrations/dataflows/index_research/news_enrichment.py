"""Enrich raw headlines with factual summaries — never trust headline text alone."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any

from trade_integrations.dataflows.index_research.causal_attribution import _NEWS_KEYWORDS
from trade_integrations.dataflows.index_research.news_tags import ArticleTags, build_article_tags
from trade_integrations.dataflows.index_research.playground_context import _headline_factor_hints

_CLICKBAIT_PREFIXES = (
    "breaking:",
    "just in:",
    "alert:",
    "exclusive:",
    "watch:",
    "live:",
    "urgent:",
)

_NUMBER_RE = re.compile(r"([+-]?\d+(?:\.\d+)?)\s*(%|percent|bps|cr|crore|bn|billion|points?|pts)", re.I)


@dataclass
class StructuredSummary:
    facts: list[str] = field(default_factory=list)
    entities: list[str] = field(default_factory=list)
    implied_factors: list[str] = field(default_factory=list)


@dataclass
class EnrichedNewsItem:
    id: str
    title: str
    url: str = ""
    source: str = ""
    published_at: str = ""
    content_summary: str = ""
    structured_summary: StructuredSummary = field(default_factory=StructuredSummary)
    tags: ArticleTags = field(default_factory=ArticleTags)
    raw_headline: str = ""

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["structured_summary"] = asdict(self.structured_summary)
        payload["tags"] = self.tags.to_dict()
        return payload


def de_clickbait_title(title: str) -> str:
    """Strip sensational framing; keep factual core."""
    text = (title or "").strip()
    lower = text.lower()
    for prefix in _CLICKBAIT_PREFIXES:
        if lower.startswith(prefix):
            text = text[len(prefix) :].strip()
            break
    text = re.sub(r"\s+", " ", text)
    return text[:500]


def _extract_entities(text: str) -> list[str]:
    lower = text.lower()
    entities: list[str] = []
    mapping = {
        "fii": "FII",
        "dii": "DII",
        "rbi": "RBI",
        "nifty": "NIFTY",
        "sensex": "SENSEX",
        "brent": "Brent",
        "crude": "Crude",
        "vix": "VIX",
        "fed": "Fed",
        "opec": "OPEC",
    }
    for key, label in mapping.items():
        if key in lower and label not in entities:
            entities.append(label)
    return entities[:8]


def _extract_facts(title: str, summary: str) -> list[str]:
    """Build fact bullets from body text; headline is secondary."""
    body = (summary or "").strip()
    title_clean = de_clickbait_title(title)
    facts: list[str] = []

    if body and body.lower() != title_clean.lower():
        sentences = re.split(r"(?<=[.!?])\s+", body)
        for sent in sentences:
            s = sent.strip()
            if len(s) < 20:
                continue
            if s.lower() == title_clean.lower():
                continue
            facts.append(s[:280])
            if len(facts) >= 4:
                break

    if not facts and title_clean:
        facts.append(title_clean[:280])

    for match in _NUMBER_RE.finditer(f"{title} {summary}"):
        snippet = match.group(0).strip()
        fact = f"Reported figure: {snippet}"
        if fact not in facts:
            facts.append(fact)

    return facts[:6]


def build_structured_summary(title: str, summary: str = "") -> StructuredSummary:
    title_clean = de_clickbait_title(title)
    working = f"{summary} {title_clean}".strip()
    implied = _headline_factor_hints(title_clean)
    if not implied and summary:
        implied = _headline_factor_hints(summary)
    entities = _extract_entities(working)
    for tag in _NEWS_KEYWORDS:
        if any(kw in working.lower() for kw in _NEWS_KEYWORDS[tag]):
            if tag.upper() not in entities:
                entities.append(tag.upper())
    return StructuredSummary(
        facts=_extract_facts(title, summary),
        entities=entities,
        implied_factors=implied or ["index_sentiment"],
    )


def build_content_summary(title: str, summary: str = "") -> str:
    """Human-readable summary preferring article body over headline."""
    body = (summary or "").strip()
    title_clean = de_clickbait_title(title)
    if body and body.lower() != title_clean.lower():
        return body[:1200]
    return title_clean


def enrich_headline(
    *,
    headline_id: str,
    title: str,
    summary: str = "",
    url: str = "",
    source: str = "",
    published_at: str = "",
    ticker: str = "NIFTY",
) -> EnrichedNewsItem:
    structured = build_structured_summary(title, summary)
    tags = build_article_tags(
        title,
        summary,
        ticker=ticker,
        published_at=published_at,
        implied_factors=structured.implied_factors,
    )
    return EnrichedNewsItem(
        id=headline_id,
        title=de_clickbait_title(title),
        url=url,
        source=source,
        published_at=published_at,
        content_summary=build_content_summary(title, summary),
        structured_summary=structured,
        tags=tags,
        raw_headline=title,
    )
