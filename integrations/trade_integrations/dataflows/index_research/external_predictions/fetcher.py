"""SearXNG search and article fetch for external predictions."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse

from trade_integrations.dataflows.article_body import fetch_article_body
from trade_integrations.dataflows.index_research.external_predictions.domain_utils import (
    attribution_name_tokens,
    native_domains,
    normalize_domain,
    url_matches_bank_topic,
)
from trade_integrations.dataflows.index_research.external_predictions.models import (
    ExternalPredictionRecord,
    ExternalPredictionSource,
)
from trade_integrations.dataflows.index_research.external_predictions.source_registry import (
    format_queries,
)
from trade_integrations.dataflows.index_research.external_predictions.url_policy import (
    is_allowed_url,
    is_candidate_article_url,
    is_listing_page_url,
    link_has_forecast_signal,
)
from trade_integrations.dataflows.index_research.pipeline_log import PipelineLogger
from trade_integrations.dataflows.searxng_finance import search_finance

logger = logging.getLogger(__name__)

_DISCOVERY_URL_LIMIT = 5


def _discovery_url_limit() -> int:
    return _DISCOVERY_URL_LIMIT


def _url_rank_adjustment(url: str, title: str = "") -> float:
    adj = 0.0
    path = urlparse(url).path.lower()
    if "/articleshow/" in path or "/blog/" in path:
        adj += 2.0
    if is_listing_page_url(url):
        adj -= 3.0
    if link_has_forecast_signal(f"{title} {url}"):
        adj += 1.0
    return adj


_RELEVANCE_TERMS = (
    "nifty",
    "nifty 50",
    "nifty50",
    "target",
    "forecast",
    "outlook",
    "view",
)


@dataclass
class SearxngSearchOutcome:
    hits: list[dict[str, Any]] = field(default_factory=list)
    queries_run: int = 0
    queries_failed: int = 0
    domain_filter_exhausted: bool = False

    @property
    def all_queries_failed(self) -> bool:
        return self.queries_run > 0 and self.queries_failed >= self.queries_run

    @property
    def empty(self) -> bool:
        return not self.hits


@dataclass
class SearxngDiscoveryResult:
    urls: list[str] = field(default_factory=list)
    hits: list[dict[str, Any]] = field(default_factory=list)
    queries_run: int = 0
    queries_failed: int = 0
    discovery_failed: bool = False
    domain_filter_exhausted: bool = False

    @property
    def all_queries_failed(self) -> bool:
        return self.queries_run > 0 and self.queries_failed >= self.queries_run


def _domain(host: str) -> str:
    return (host or "").lower().removeprefix("www.")


def _normalize_source_domain(domain: str) -> str:
    return normalize_domain(domain)


def _source_allowed_domains(source: ExternalPredictionSource) -> tuple[str, ...]:
    native = native_domains(source)
    if native:
        return native
    syndicated: list[str] = []
    for domain in source.domains or []:
        norm = normalize_domain(domain)
        if norm:
            syndicated.append(norm)
    return tuple(dict.fromkeys(syndicated))


def _matches_source_domain(url: str, source: ExternalPredictionSource) -> bool:
    host = _domain(urlparse(url).hostname or "")
    if not host:
        return False
    for domain in source.domains:
        d = _normalize_source_domain(domain)
        if not d:
            continue
        if host == d or host.endswith(f".{d}"):
            return True
    return False


def _relevance_score(result: dict[str, Any], source: ExternalPredictionSource) -> float:
    blob = " ".join(
        str(result.get(key) or "") for key in ("title", "content", "url")
    ).lower()
    score = 0.0
    url = str(result.get("url") or "")
    if not _matches_source_domain(url, source):
        return -1.0
    if _matches_source_domain(url, source):
        score += 3.0
    title = str(result.get("title") or "")
    if link_has_forecast_signal(f"{title} {url}"):
        score += 2.5
    for term in _RELEVANCE_TERMS:
        if term in blob:
            score += 0.5
    if any(token in blob for token in attribution_name_tokens(source)):
        score += 2.0 if source.kind == "global_bank" else 1.0
    if source.kind == "global_bank" and url_matches_bank_topic(url, source):
        score += 2.5
    if re.search(r"\d{1,2}[,.]?\d{3,5}", blob):
        score += 1.5
    title = str(result.get("title") or "")
    score += _url_rank_adjustment(url, title)
    return score


def filter_searxng_hits_for_source(
    hits: list[dict[str, Any]],
    source: ExternalPredictionSource,
) -> list[dict[str, Any]]:
    """Keep hits on source domains; prefer forecast-signal URLs."""
    domain_ok: list[dict[str, Any]] = []
    for row in hits:
        url = str(row.get("url") or "").strip()
        if not url or not _matches_source_domain(url, source):
            continue
        domain_ok.append(row)
    if not domain_ok:
        return []
    with_signal = [
        row
        for row in domain_ok
        if link_has_forecast_signal(
            f"{row.get('title') or ''} {row.get('url') or ''}"
        )
    ]
    pool = with_signal or domain_ok
    return sorted(pool, key=lambda row: _relevance_score(row, source), reverse=True)


def rank_search_results(
    results: list[dict[str, Any]],
    source: ExternalPredictionSource,
    *,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    cap = limit if limit is not None else _discovery_url_limit()
    filtered = filter_searxng_hits_for_source(results, source)
    ranked = sorted(filtered, key=lambda row: _relevance_score(row, source), reverse=True)
    return ranked[:cap]


def search_source_results_with_outcome(
    source: ExternalPredictionSource,
    *,
    horizon_days: int,
    limit: int = 8,
    pipeline: PipelineLogger | None = None,
) -> SearxngSearchOutcome:
    collected: list[dict[str, Any]] = []
    seen_urls: set[str] = set()
    queries = format_queries(source, horizon_days=horizon_days)
    allowed_domains = _source_allowed_domains(source)
    queries_run = 0
    queries_failed = 0
    domain_filter_exhausted = False
    if not allowed_domains:
        if pipeline:
            pipeline.warn(
                "searxng",
                f"No domains configured for {source.display_name} — skipping SearXNG discovery",
                source_id=source.id,
            )
        return SearxngSearchOutcome(
            hits=[],
            queries_run=0,
            queries_failed=0,
            domain_filter_exhausted=False,
        )
    if pipeline:
        pipeline.info(
            "searxng",
            f"Searching {source.display_name} ({len(queries)} queries)",
            source_id=source.id,
        )
    for query in queries:
        queries_run += 1
        if pipeline:
            pipeline.info("searxng", f"Query: {query}", source_id=source.id)
        try:
            query_stats: dict[str, int] = {}
            rows = search_finance(
                query,
                limit=limit,
                allowed_domains=allowed_domains or None,
                stats=query_stats,
            )
        except Exception as exc:
            queries_failed += 1
            if pipeline:
                pipeline.warn("searxng", f"Search failed: {exc}", source_id=source.id, query=query)
            logger.debug("search failed for %s query=%r: %s", source.id, query, exc)
            continue
        if pipeline:
            raw_count = query_stats.get("raw_count", len(rows))
            if not rows and raw_count > 0:
                domain_filter_exhausted = True
                msg = f"Got 0 result(s) after domain filter ({raw_count} raw)"
            else:
                msg = f"Got {len(rows)} result(s) for query"
            pipeline.info(
                "searxng",
                msg,
                source_id=source.id,
                query=query,
            )
        for row in rows:
            url = str(row.get("url") or "")
            if not url or url in seen_urls:
                continue
            seen_urls.add(url)
            collected.append(row)
    ranked = rank_search_results(collected, source)
    outcome = SearxngSearchOutcome(
        hits=ranked,
        queries_run=queries_run,
        queries_failed=queries_failed,
        domain_filter_exhausted=domain_filter_exhausted,
    )
    if pipeline:
        if outcome.all_queries_failed:
            pipeline.warn(
                "searxng",
                f"All {queries_run} SearXNG queries failed for {source.display_name}",
                source_id=source.id,
            )
        elif len(collected) > 0 and len(ranked) == 0:
            pipeline.warn(
                "searxng",
                (
                    f"Rank/forecast filter removed all {len(collected)} SearXNG hit(s) "
                    f"for {source.display_name}"
                ),
                source_id=source.id,
            )
        else:
            pipeline.info(
                "searxng",
                f"Ranked {len(ranked)} candidate article(s) for {source.display_name}",
                source_id=source.id,
                queries_failed=queries_failed,
            )
    return outcome


def search_source_results(
    source: ExternalPredictionSource,
    *,
    horizon_days: int,
    limit: int = 8,
    pipeline: PipelineLogger | None = None,
) -> list[dict[str, Any]]:
    return search_source_results_with_outcome(
        source,
        horizon_days=horizon_days,
        limit=limit,
        pipeline=pipeline,
    ).hits


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


def filter_discovery_urls(
    results: list[dict[str, Any]],
    source: ExternalPredictionSource,
    *,
    limit: int | None = None,
) -> list[str]:
    """Return allowlisted article URLs from SearXNG hits."""
    cap = limit if limit is not None else _discovery_url_limit()
    urls: list[str] = []
    seen: set[str] = set()
    for row in results:
        url = str(row.get("url") or "").strip()
        title = str(row.get("title") or "")
        if not url or url in seen:
            continue
        policy = is_allowed_url(url, title=title)
        if not policy.allowed and not is_candidate_article_url(url, title=title).allowed:
            continue
        seen.add(url)
        urls.append(url)
        if len(urls) >= cap:
            break
    return urls


def discover_source_with_results(
    source: ExternalPredictionSource,
    *,
    horizon_days: int,
    pipeline: PipelineLogger | None = None,
) -> SearxngDiscoveryResult:
    outcome = search_source_results_with_outcome(
        source,
        horizon_days=horizon_days,
        pipeline=pipeline,
    )
    urls = filter_discovery_urls(outcome.hits, source)
    if pipeline and outcome.hits and not urls:
        pipeline.warn(
            "searxng",
            (
                f"URL policy removed all {len(outcome.hits)} ranked hit(s) "
                f"for {source.display_name}"
            ),
            source_id=source.id,
        )
    return SearxngDiscoveryResult(
        urls=urls,
        hits=list(outcome.hits),
        queries_run=outcome.queries_run,
        queries_failed=outcome.queries_failed,
        domain_filter_exhausted=outcome.domain_filter_exhausted,
    )


def discover_source_urls(
    source: ExternalPredictionSource,
    *,
    horizon_days: int,
    pipeline: PipelineLogger | None = None,
) -> list[str]:
    return discover_source_with_results(
        source,
        horizon_days=horizon_days,
        pipeline=pipeline,
    ).urls


def discover_sources_parallel(
    sources: list[ExternalPredictionSource],
    *,
    horizon_days: int,
    pipeline: PipelineLogger | None = None,
) -> dict[str, SearxngDiscoveryResult]:
    """Run SearXNG discovery for each source in parallel (single search per source per batch)."""
    from concurrent.futures import ThreadPoolExecutor, as_completed

    if not sources:
        return {}

    out: dict[str, SearxngDiscoveryResult] = {
        source.id: SearxngDiscoveryResult() for source in sources
    }
    max_workers = min(8, len(sources))
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {
            pool.submit(
                discover_source_with_results,
                source,
                horizon_days=horizon_days,
                pipeline=pipeline,
            ): source
            for source in sources
        }
        for future in as_completed(futures):
            source = futures[future]
            try:
                out[source.id] = future.result()
            except Exception as exc:
                if pipeline:
                    pipeline.warn(
                        "searxng",
                        f"Parallel discovery failed: {exc}",
                        source_id=source.id,
                    )
                logger.debug("parallel discovery failed for %s: %s", source.id, exc)
                out[source.id] = SearxngDiscoveryResult(
                    queries_run=1,
                    queries_failed=1,
                    discovery_failed=True,
                )
    return out


def discovery_urls_map(
    discovery: dict[str, SearxngDiscoveryResult],
) -> dict[str, list[str]]:
    return {source_id: bundle.urls for source_id, bundle in discovery.items()}


def extract_via_searxng_fallback(
    source: ExternalPredictionSource,
    *,
    symbol: str = "NIFTY",
    horizon_days: int = 14,
    spot: float | None = None,
    pipeline: PipelineLogger | None = None,
    search_outcome: SearxngSearchOutcome | SearxngDiscoveryResult | None = None,
) -> ExternalPredictionRecord | None:
    """
    Text-only fallback when Crawl4AI is blocked — search SearXNG and extract without screenshots.
    """
    from trade_integrations.dataflows.index_research.external_predictions.crawl4ai_fetcher import (
        filter_markdown_for_extraction,
        source_keywords,
    )
    from trade_integrations.dataflows.index_research.external_predictions.financial_expert_agent import (
        extract_forecast,
    )
    from trade_integrations.dataflows.index_research.external_predictions.models import (
        utc_now_iso,
    )

    sym = symbol.upper()
    if pipeline:
        pipeline.info(
            "searxng",
            f"SearXNG text fallback for {source.display_name}",
            source_id=source.id,
        )

    keywords = source_keywords(source, horizon_days=horizon_days)
    if search_outcome is not None:
        if isinstance(search_outcome, SearxngDiscoveryResult):
            if search_outcome.discovery_failed:
                if pipeline:
                    pipeline.info(
                        "searxng",
                        f"Discovery failed earlier — running direct SearXNG search for {source.display_name}",
                        source_id=source.id,
                    )
                outcome = search_source_results_with_outcome(
                    source,
                    horizon_days=horizon_days,
                    pipeline=pipeline,
                )
                hits = outcome.hits
            else:
                hits = list(search_outcome.hits)
                outcome = SearxngSearchOutcome(
                    hits=hits,
                    queries_run=search_outcome.queries_run,
                    queries_failed=search_outcome.queries_failed,
                )
                if pipeline:
                    pipeline.info(
                        "searxng",
                        f"Reusing cached SearXNG hits for {source.display_name} ({len(hits)} ranked)",
                        source_id=source.id,
                    )
        else:
            outcome = search_outcome
            hits = list(outcome.hits)
    else:
        outcome = search_source_results_with_outcome(
            source,
            horizon_days=horizon_days,
            pipeline=pipeline,
        )
        hits = outcome.hits

    if outcome.all_queries_failed:
        if pipeline:
            pipeline.warn(
                "searxng",
                f"SearXNG fallback skipped — all queries failed for {source.display_name}",
                source_id=source.id,
            )
        return None

    hits = filter_searxng_hits_for_source(hits, source)
    if not hits:
        if pipeline:
            pipeline.warn(
                "searxng",
                f"SearXNG fallback — no domain-valid hits for {source.display_name}",
                source_id=source.id,
            )
        return None

    last_record: ExternalPredictionRecord | None = None

    for hit in hits:
        title, url, body = fetch_source_content(hit, pipeline=pipeline, source_id=source.id)
        if len(body.strip()) < 80:
            continue
        filtered = filter_markdown_for_extraction(
            body,
            keywords,
            horizon_days=horizon_days,
        )
        snippet = "\n".join(filtered.splitlines()[:12])
        record = extract_forecast(
            source=source,
            horizon_days=horizon_days,
            spot=spot,
            title=title or source.display_name,
            url=url,
            snippet=snippet,
            body=filtered,
            symbol=sym,
            screenshot_artifacts=None,
            pipeline=pipeline,
        )
        record.as_of = utc_now_iso()[:10]
        record.spot_at_fetch = spot
        record.provenance = {
            **dict(record.provenance or {}),
            "url": url,
            "title": title or record.provenance.get("title", ""),
            "fetch_method": "searxng_text",
            "navigation_mode": "searxng_fallback",
        }
        last_record = record
        if record.fetch_status == "ok":
            if pipeline:
                pipeline.info(
                    "searxng",
                    f"Fallback extracted forecast from {url[:100]}",
                    source_id=source.id,
                )
            return record

    return last_record
