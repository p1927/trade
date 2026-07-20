"""SearXNG search and article fetch for external predictions."""

from __future__ import annotations

import logging
import re
from typing import Any
from urllib.parse import urlparse

from trade_integrations.dataflows.article_body import fetch_article_body
from trade_integrations.dataflows.index_research.external_predictions.models import (
    ExternalPredictionSource,
)
from trade_integrations.dataflows.index_research.external_predictions.source_registry import (
    format_queries,
)
from trade_integrations.dataflows.index_research.pipeline_log import PipelineLogger
from trade_integrations.dataflows.searxng_finance import search_finance

logger = logging.getLogger(__name__)

_RELEVANCE_TERMS = (
    "nifty",
    "nifty 50",
    "nifty50",
    "target",
    "forecast",
    "outlook",
    "view",
)


def _domain(host: str) -> str:
    return (host or "").lower().removeprefix("www.")


def _matches_source_domain(url: str, source: ExternalPredictionSource) -> bool:
    host = _domain(urlparse(url).hostname or "")
    if not host:
        return False
    for domain in source.domains:
        d = domain.lower().removeprefix("www.")
        if host == d or host.endswith(f".{d}"):
            return True
    return False


def _relevance_score(result: dict[str, Any], source: ExternalPredictionSource) -> float:
    blob = " ".join(
        str(result.get(key) or "") for key in ("title", "content", "url")
    ).lower()
    score = 0.0
    if _matches_source_domain(str(result.get("url") or ""), source):
        score += 3.0
    for term in _RELEVANCE_TERMS:
        if term in blob:
            score += 0.5
    if source.display_name.lower() in blob:
        score += 1.0
    if re.search(r"\d{1,2}[,.]?\d{3,5}", blob):
        score += 1.5
    return score


def rank_search_results(
    results: list[dict[str, Any]],
    source: ExternalPredictionSource,
    *,
    limit: int = 3,
) -> list[dict[str, Any]]:
    ranked = sorted(results, key=lambda row: _relevance_score(row, source), reverse=True)
    return ranked[:limit]


def search_source_results(
    source: ExternalPredictionSource,
    *,
    horizon_days: int,
    limit: int = 8,
    pipeline: PipelineLogger | None = None,
) -> list[dict[str, Any]]:
    collected: list[dict[str, Any]] = []
    seen_urls: set[str] = set()
    queries = format_queries(source, horizon_days=horizon_days)
    if pipeline:
        pipeline.info(
            "searxng",
            f"Searching {source.display_name} ({len(queries)} queries)",
            source_id=source.id,
        )
    for query in queries:
        if pipeline:
            pipeline.info("searxng", f"Query: {query}", source_id=source.id)
        try:
            rows = search_finance(query, limit=limit)
        except Exception as exc:
            if pipeline:
                pipeline.warn("searxng", f"Search failed: {exc}", source_id=source.id, query=query)
            logger.debug("search failed for %s query=%r: %s", source.id, query, exc)
            continue
        if pipeline:
            pipeline.info(
                "searxng",
                f"Got {len(rows)} result(s) for query",
                source_id=source.id,
                query=query,
            )
        for row in rows:
            url = str(row.get("url") or "")
            if not url or url in seen_urls:
                continue
            seen_urls.add(url)
            collected.append(row)
    ranked = rank_search_results(collected, source, limit=3)
    if pipeline:
        pipeline.info(
            "searxng",
            f"Ranked {len(ranked)} candidate article(s) for {source.display_name}",
            source_id=source.id,
        )
    return ranked


def fetch_source_content(
    result: dict[str, Any],
    *,
    pipeline: PipelineLogger | None = None,
    source_id: str = "",
) -> tuple[str, str, str]:
    """Return (title, url, body_or_snippet)."""
    url = str(result.get("url") or "")
    title = str(result.get("title") or "")
    snippet = str(result.get("content") or "")
    body = ""
    if url:
        if pipeline:
            pipeline.info(
                "article",
                f"Fetching article: {title[:80] or url[:80]}",
                source_id=source_id,
                url=url,
            )
        fetched = fetch_article_body(url)
        if fetched:
            body = fetched
            if pipeline:
                pipeline.info(
                    "article",
                    f"Article body fetched ({len(body)} chars)",
                    source_id=source_id,
                    url=url,
                )
        elif pipeline:
            pipeline.warn(
                "article",
                "Article fetch failed — using search snippet only",
                source_id=source_id,
                url=url,
            )
    return title, url, body or snippet
