"""Crawl4AI fetch helpers for external predictions."""

from __future__ import annotations

import logging
import os
import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlparse

from trade_integrations.dataflows.crawl4ai_client import (
    CrawlPageResult,
    crawl_urls_parallel_sync,
)
from trade_integrations.dataflows.index_research.external_predictions.curated_urls import (
    curated_urls_for_source,
)
from trade_integrations.dataflows.index_research.external_predictions.models import (
    ExternalPredictionSource,
)
from trade_integrations.dataflows.index_research.external_predictions.store import (
    load_source_prediction,
)
from trade_integrations.dataflows.index_research.external_predictions.url_policy import (
    MIN_KEYWORD_SCORE,
    is_allowed_listing_url,
    is_allowed_url,
    is_article_url,
    is_candidate_article_url,
    link_score,
    markdown_has_nifty50_forecast,
)
from trade_integrations.dataflows.index_research.pipeline_log import PipelineLogger

logger = logging.getLogger(__name__)

_DEFAULT_KEYWORDS: tuple[str, ...] = (
    "forecast",
    "target",
    "prediction",
    "outlook",
    "analyst",
    "nifty",
    "nifty 50",
    "nifty50",
)

_NIFTY_LINE = re.compile(r"nifty\s*50|nifty50|\bnifty\b", re.I)
_TARGET_LINE = re.compile(r"target|forecast|outlook|view|prediction|analyst", re.I)
_MD_LINK = re.compile(r"\[([^\]]*)\]\((https?://[^)]+)\)", re.I)


@dataclass
class LinkDiscoveryStats:
    seen: int = 0
    kept: int = 0
    skip_reasons: Counter[str] = field(default_factory=Counter)

    def record_skip(self, reason: str) -> None:
        self.skip_reasons[reason] += 1

    def summary(self) -> str:
        parts = [f"{count} {reason}" for reason, count in sorted(self.skip_reasons.items())]
        skipped = ", ".join(parts) if parts else "0 skipped"
        return f"Link discovery: {self.seen} seen, {self.kept} kept, {skipped}"


def format_link_discovery_summary(stats: LinkDiscoveryStats) -> str:
    return stats.summary()


def default_search_keywords() -> list[str]:
    return list(_DEFAULT_KEYWORDS)


def _article_candidates_limit() -> int:
    raw = os.environ.get("EXTERNAL_PREDICTIONS_ARTICLE_CANDIDATES", "3").strip()
    try:
        return max(1, int(raw))
    except ValueError:
        return 3


def source_keywords(source: ExternalPredictionSource, *, horizon_days: int = 14) -> list[str]:
    keys = list(source.search_keywords) if source.search_keywords else list(_DEFAULT_KEYWORDS)
    keys.extend(horizon_keywords(horizon_days))
    return list(dict.fromkeys(k for k in keys if str(k).strip()))


def horizon_keywords(horizon_days: int) -> list[str]:
    """Keywords that help match articles for the selected forecast horizon."""
    keys = [f"{horizon_days} day", f"{horizon_days}d", f"{horizon_days}-day"]
    if horizon_days <= 7:
        keys.extend(["week", "weekly", "short term", "near term"])
    elif horizon_days <= 21:
        keys.extend(["2 week", "3 week", "fortnight"])
    elif horizon_days <= 45:
        keys.extend(["month", "monthly", "30 day", "one month"])
    else:
        keys.extend(["quarter", "medium term", "60 day", "two month"])
    return keys


def _format_url_template(url: str, *, horizon_days: int) -> str:
    year = str(datetime.now(timezone.utc).year)
    return (
        url.replace("{horizon}", str(horizon_days))
        .replace("{year}", year)
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


def resolve_source_urls(
    source: ExternalPredictionSource,
    *,
    symbol: str = "NIFTY",
    horizon_days: int = 14,
) -> list[str]:
    """Return allowlisted crawl targets (curated URLs + last ok article if policy passes)."""
    urls: list[str] = []
    seen: set[str] = set()

    raw_list = list(source.curated_urls or []) or list(curated_urls_for_source(source.id))
    if not raw_list:
        raw_list = list(source.landing_urls or [])

    for raw in raw_list:
        u = _format_url_template(str(raw or "").strip(), horizon_days=horizon_days)
        if not u or u in seen:
            continue
        policy = is_allowed_listing_url(u)
        if not policy.allowed:
            continue
        seen.add(u)
        urls.append(u)

    prior = load_source_prediction(source.id, symbol=symbol, horizon_days=horizon_days)
    if prior and prior.fetch_status == "ok" and prior.provenance:
        last_url = str(prior.provenance.get("url") or "").strip()
        title = str(prior.provenance.get("title") or "")
        if last_url and last_url not in seen:
            policy = is_allowed_url(last_url, title=title)
            if policy.allowed:
                seen.add(last_url)
                urls.append(last_url)
    return urls


def _iter_link_candidates(
    markdown: str,
    native_links: list[dict[str, Any]] | None,
) -> list[tuple[str, str, float | None]]:
    """Yield (title, url, native_score) from Crawl4AI links and markdown fallbacks."""
    rows: list[tuple[str, str, float | None]] = []
    seen: set[str] = set()

    for item in native_links or []:
        href = str(item.get("href") or "").strip().split()[0]
        if not href or href in seen:
            continue
        seen.add(href)
        title = str(item.get("text") or item.get("title") or "").strip()
        native = item.get("total_score")
        native_score = float(native) if isinstance(native, (int, float)) else None
        rows.append((title, href, native_score))

    for title, url in _MD_LINK.findall(markdown or ""):
        u = url.strip().split()[0] if url.strip() else ""
        if not u or u in seen:
            continue
        seen.add(u)
        rows.append((title.strip(), u, None))

    return rows


def extract_article_links(
    markdown: str,
    source: ExternalPredictionSource,
    *,
    limit: int | None = None,
    native_links: list[dict[str, Any]] | None = None,
    pipeline: PipelineLogger | None = None,
) -> list[str]:
    """Pull ranked article URLs from listing markdown / Crawl4AI native links."""
    candidate_limit = limit if limit is not None else _article_candidates_limit()
    stats = LinkDiscoveryStats()
    scored: list[tuple[float, str]] = []
    seen_urls: set[str] = set()

    for title, url, native_score in _iter_link_candidates(markdown, native_links):
        stats.seen += 1
        if url in seen_urls:
            stats.record_skip("duplicate")
            continue
        if not _matches_source_domain(url, source):
            stats.record_skip("wrong_domain")
            continue
        policy = is_candidate_article_url(url, title=title)
        if not policy.allowed:
            stats.record_skip(policy.reason)
            continue
        seen_urls.add(url)
        score = link_score(title, url, native_score=native_score)
        scored.append((score, url))

    scored.sort(key=lambda item: item[0], reverse=True)
    out = [url for _, url in scored[:candidate_limit]]
    stats.kept = len(out)

    if pipeline and stats.seen:
        pipeline.info(
            "crawl4ai",
            stats.summary(),
            source_id=source.id,
            kept_urls=[u[:120] for u in out],
        )
    elif stats.seen:
        logger.debug("%s [%s]", stats.summary(), source.id)

    return out


def filter_markdown_for_extraction(
    markdown: str,
    keywords: list[str] | None = None,
    *,
    horizon_days: int = 14,
    max_chars: int = 8000,
) -> str:
    """Keep keyword-relevant lines; fall back to truncated full markdown."""
    text = (markdown or "").strip()
    if not text:
        return ""
    keys = [k.lower() for k in (keywords or list(_DEFAULT_KEYWORDS)) if str(k).strip()]
    keys.extend(k.lower() for k in horizon_keywords(horizon_days))
    matching: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if len(stripped) < 15:
            continue
        lower = stripped.lower()
        if any(k in lower for k in keys):
            matching.append(stripped)
    if matching:
        body = "\n".join(matching)
    else:
        body = text
    if len(body) > max_chars:
        return body[:max_chars]
    return body


def keyword_match_score(
    markdown: str,
    keywords: list[str] | None = None,
    *,
    horizon_days: int = 14,
) -> float:
    text = (markdown or "").lower()
    if not text:
        return 0.0
    score = 0.0
    keys = list(keywords or list(_DEFAULT_KEYWORDS))
    keys.extend(horizon_keywords(horizon_days))
    for key in keys:
        if key.lower() in text:
            score += 1.0
    if _NIFTY_LINE.search(text):
        score += 2.0
    if _TARGET_LINE.search(text):
        score += 1.5
    if re.search(r"\d{1,2}[,.]?\d{3,5}", text):
        score += 1.0
    return score


def crawl_sources_parallel(
    sources: list[ExternalPredictionSource],
    *,
    symbol: str = "NIFTY",
    horizon_days: int = 14,
    pipeline: PipelineLogger | None = None,
) -> dict[str, list[tuple[str, CrawlPageResult]]]:
    """Crawl curated URLs; then top-ranked article candidates per source."""
    grouped: dict[str, list[tuple[str, CrawlPageResult]]] = {source.id: [] for source in sources}

    landing_jobs: list[tuple[str, str]] = []
    for source in sources:
        for url in resolve_source_urls(source, symbol=symbol, horizon_days=horizon_days):
            landing_jobs.append((source.id, url))

    if not landing_jobs:
        return grouped

    if pipeline:
        pipeline.info(
            "crawl4ai",
            f"Phase 1 — curated NIFTY 50 URLs for {horizon_days}d horizon ({len(landing_jobs)} URL(s))",
            horizon_days=horizon_days,
        )

    landing_results = crawl_urls_parallel_sync(
        [url for _, url in landing_jobs],
        pipeline=pipeline,
        score_links=True,
    )
    source_by_id = {source.id: source for source in sources}
    per_source_limit = _article_candidates_limit()

    article_jobs: list[tuple[str, str]] = []
    seen_article_urls: set[str] = set()
    for (source_id, landing_url), result in zip(landing_jobs, landing_results):
        grouped[source_id].append((landing_url, result))
        if not result.success:
            continue
        source = source_by_id.get(source_id)
        if source is None:
            continue
        native_links = result.metadata.get("links") if isinstance(result.metadata, dict) else None
        for article_url in extract_article_links(
            result.markdown,
            source,
            limit=per_source_limit,
            native_links=native_links if isinstance(native_links, list) else None,
            pipeline=pipeline,
        ):
            if article_url in seen_article_urls:
                continue
            seen_article_urls.add(article_url)
            article_jobs.append((source_id, article_url))

    if article_jobs:
        if pipeline:
            pipeline.info(
                "crawl4ai",
                f"Phase 2 — ranked forecast article candidates ({len(article_jobs)} URL(s))",
                horizon_days=horizon_days,
            )
        article_results = crawl_urls_parallel_sync(
            [url for _, url in article_jobs],
            pipeline=pipeline,
        )
        for (source_id, article_url), result in zip(article_jobs, article_results):
            grouped.setdefault(source_id, []).append((article_url, result))

    return grouped


def pick_best_crawl_result(
    rows: list[tuple[str, CrawlPageResult]],
    keywords: list[str] | None = None,
    *,
    horizon_days: int = 14,
    pipeline: PipelineLogger | None = None,
) -> tuple[str, CrawlPageResult] | None:
    """Prefer article pages with strong NIFTY 50 index forecast signal in page body."""
    candidates: list[tuple[str, CrawlPageResult, float]] = []
    for url, row in rows:
        if not row.success or not row.markdown.strip():
            continue
        policy = is_allowed_listing_url(url)
        if not policy.allowed:
            if pipeline:
                pipeline.info("crawl4ai", f"Skipped crawl ({policy.reason})", url=url[:120])
            continue
        if not markdown_has_nifty50_forecast(row.markdown):
            if pipeline:
                pipeline.info("crawl4ai", "Skipped crawl (no_nifty50_target)", url=url[:120])
            continue
        score = keyword_match_score(row.markdown, keywords, horizon_days=horizon_days)
        if is_article_url(url):
            score += 3.0
        if score >= MIN_KEYWORD_SCORE:
            candidates.append((url, row, score))

    if not candidates:
        return None
    best = max(candidates, key=lambda item: item[2])
    return best[0], best[1]
