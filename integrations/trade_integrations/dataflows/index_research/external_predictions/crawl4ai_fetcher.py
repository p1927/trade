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
from trade_integrations.dataflows.index_research.external_predictions.crawl_resilience import (
    sort_urls_for_crawl,
)
from trade_integrations.dataflows.index_research.external_predictions.batch_url_dedup import (
    dedupe_crawl_article_jobs,
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
from trade_integrations.dataflows.index_research.external_predictions.extractor import (
    crawl_result_horizon_boost,
)
from trade_integrations.dataflows.index_research.external_predictions.url_policy import (
    MIN_KEYWORD_SCORE,
    is_allowed_listing_url,
    is_allowed_url,
    is_candidate_article_url,
    is_structured_forecast_hub_url,
    link_has_forecast_signal,
    link_score,
    markdown_has_nifty50_forecast,
    url_selection_penalty,
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
    discovery_urls: list[str] | None = None,
) -> list[str]:
    """Return allowlisted crawl targets — entry URLs first, blocklisted domains last."""
    ordered_raw: list[str] = []

    for raw in source.entry_urls or []:
        ordered_raw.append(str(raw or "").strip())

    for extra in (discovery_urls or [])[: _discovery_urls_limit()]:
        ordered_raw.append(str(extra or "").strip())

    prior = load_source_prediction(source.id, symbol=symbol, horizon_days=horizon_days)
    last_ok_url = ""
    if prior and prior.fetch_status == "ok" and prior.provenance:
        last_ok_url = str(prior.provenance.get("url") or "").strip()
        if last_ok_url:
            ordered_raw.append(last_ok_url)

    for raw in source.landing_urls or []:
        ordered_raw.append(str(raw or "").strip())
    curated_list = list(source.curated_urls or []) or list(curated_urls_for_source(source.id))
    ordered_raw.extend(str(raw or "").strip() for raw in curated_list)

    urls: list[str] = []
    seen: set[str] = set()
    for raw in ordered_raw:
        u = _format_url_template(raw, horizon_days=horizon_days)
        if not u or u in seen:
            continue
        if u == last_ok_url and prior and prior.provenance:
            policy = is_allowed_url(u, title=str(prior.provenance.get("title") or ""))
            if policy.allowed:
                seen.add(u)
                urls.append(u)
                continue
            # Fall through to listing/article policy — article URLs may fail is_allowed_url
            # but still be valid retry targets from a prior successful fetch.
        policy = is_allowed_listing_url(u)
        if policy.allowed:
            seen.add(u)
            urls.append(u)
            continue
        article_policy = is_candidate_article_url(u, title=source.display_name)
        url_policy = is_allowed_url(u, title=source.display_name)
        if url_policy.allowed or article_policy.allowed:
            seen.add(u)
            urls.append(u)

    return sort_urls_for_crawl(urls)


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
    """Pull ranked forecast URLs from listing markdown / Crawl4AI native links."""
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



def crawl_single_url(
    url: str,
    *,
    pipeline: PipelineLogger | None = None,
) -> tuple[str, CrawlPageResult] | None:
    """Crawl one URL with screenshot capture (sequential, max_parallel=1)."""
    cleaned = str(url or "").strip()
    if not cleaned:
        return None
    results = crawl_urls_parallel_sync(
        [cleaned],
        max_parallel=1,
        pipeline=pipeline,
        capture_screenshot=True,
    )
    if not results:
        return None
    return (cleaned, results[0])



def crawl_sources_parallel(
    sources: list[ExternalPredictionSource],
    *,
    symbol: str = "NIFTY",
    horizon_days: int = 14,
    pipeline: PipelineLogger | None = None,
    discovery_urls: dict[str, list[str]] | None = None,
    attribution_owners: dict[str, str] | None = None,
) -> dict[str, list[tuple[str, CrawlPageResult]]]:
    """Crawl curated URLs; then top-ranked article candidates per source."""
    grouped: dict[str, list[tuple[str, CrawlPageResult]]] = {source.id: [] for source in sources}

    landing_jobs: list[tuple[str, str]] = []
    for source in sources:
        extras = (discovery_urls or {}).get(source.id) or []
        for url in resolve_source_urls(
            source,
            symbol=symbol,
            horizon_days=horizon_days,
            discovery_urls=extras,
        ):
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

    article_candidates: list[tuple[str, str]] = []
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
            article_candidates.append((source_id, article_url))

    article_jobs = dedupe_crawl_article_jobs(
        article_candidates,
        sources,
        attribution_owners=attribution_owners,
    )

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
            capture_screenshot=True,
        )
        for (source_id, article_url), result in zip(article_jobs, article_results):
            grouped.setdefault(source_id, []).append((article_url, result))

    return grouped


def _discovery_urls_limit() -> int:
    raw = os.environ.get("EXTERNAL_PREDICTIONS_DISCOVERY_URLS", "5").strip()
    try:
        return max(1, int(raw))
    except ValueError:
        return 5


def _url_pick_tier(url: str) -> int:
    if is_candidate_article_url(url).allowed or "/articleshow/" in url or "/blog/" in url:
        return 1
    if is_structured_forecast_hub_url(url):
        return 2
    return 3


def rank_crawl_results(
    rows: list[tuple[str, CrawlPageResult]],
    keywords: list[str] | None = None,
    *,
    horizon_days: int = 14,
    pipeline: PipelineLogger | None = None,
    batch_registry: Any | None = None,
    source: ExternalPredictionSource | None = None,
) -> list[tuple[str, CrawlPageResult]]:
    """Return crawl candidates sorted best-first."""
    from trade_integrations.dataflows.index_research.external_predictions.crawl_resilience import (
        is_akamai_wrapped_markdown,
    )

    candidates: list[tuple[str, CrawlPageResult, float, int]] = []
    for url, row in rows:
        if batch_registry is not None and source is not None:
            crawl_rows = batch_registry.filter_crawl_rows([(url, row)], source=source)
            if not crawl_rows:
                if pipeline:
                    pipeline.info(
                        "crawl4ai",
                        "Skipped crawl (batch_url_claimed)",
                        url=url[:120],
                        source_id=source.id,
                    )
                continue
            url, row = crawl_rows[0]
        if not row.success or not row.markdown.strip():
            continue
        if is_akamai_wrapped_markdown(row.markdown, url):
            if pipeline:
                pipeline.info("crawl4ai", "Skipped crawl (akamai_wrapped)", url=url[:120])
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
        score += crawl_result_horizon_boost(row.markdown, horizon_days=horizon_days)
        score += url_selection_penalty(url)
        title_blob = f"{row.title or ''} {url}"
        if link_has_forecast_signal(title_blob):
            score += 1.5
        tier = _url_pick_tier(url)
        if score >= MIN_KEYWORD_SCORE:
            candidates.append((url, row, score, tier))

    if not candidates:
        return []

    best_tier = min(item[3] for item in candidates)
    tier_pool = [item for item in candidates if item[3] == best_tier]
    tier_pool.sort(key=lambda item: item[2], reverse=True)
    return [(url, row) for url, row, _, _ in tier_pool]


def pick_best_crawl_result(
    rows: list[tuple[str, CrawlPageResult]],
    keywords: list[str] | None = None,
    *,
    horizon_days: int = 14,
    pipeline: PipelineLogger | None = None,
    batch_registry: Any | None = None,
    source: ExternalPredictionSource | None = None,
) -> tuple[str, CrawlPageResult] | None:
    """Prefer forecast articles and structured hubs over listing/topic pages."""
    ranked = rank_crawl_results(
        rows,
        keywords,
        horizon_days=horizon_days,
        pipeline=pipeline,
        batch_registry=batch_registry,
        source=source,
    )
    if not ranked:
        return None
    return ranked[0]
