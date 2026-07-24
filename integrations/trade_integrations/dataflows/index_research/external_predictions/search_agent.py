"""Progressive SearXNG search agent for external predictions."""

from __future__ import annotations

import heapq
import logging
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse

from trade_integrations.dataflows.index_research.external_predictions.batch_url_dedup import (
    BatchUrlRegistry,
)
from trade_integrations.dataflows.index_research.external_predictions.crawl4ai_fetcher import (
    crawl_single_url,
    filter_markdown_for_extraction,
    source_keywords,
)
from trade_integrations.dataflows.index_research.external_predictions.domain_utils import (
    has_stronger_attribution,
    host_from_url,
    is_syndication_domain,
)
from trade_integrations.dataflows.index_research.external_predictions.fetcher import (
    discovery_search_passes,
    filter_discovery_hits,
    score_discovery_hit,
)
from trade_integrations.dataflows.index_research.external_predictions.financial_expert_agent import (
    extract_forecast,
)
from trade_integrations.dataflows.index_research.external_predictions.models import (
    ExternalPredictionRecord,
    ExternalPredictionSource,
    utc_now_iso,
)
from trade_integrations.dataflows.index_research.external_predictions.query_builder import (
    build_fallback_queries,
    build_horizon_context,
    load_nifty_trading_dates,
)
from trade_integrations.dataflows.index_research.external_predictions.screenshot_utils import (
    ScreenshotArtifacts,
    persist_screenshot_b64,
)
from trade_integrations.dataflows.index_research.external_predictions.source_registry import (
    format_queries,
)
from trade_integrations.dataflows.index_research.external_predictions.url_policy import (
    is_allowed_url,
    is_candidate_article_url,
)
from trade_integrations.dataflows.index_research.pipeline_log import PipelineLogger
from trade_integrations.dataflows.searxng_client import parse_engine_list, searxng_finance_engines
from trade_integrations.dataflows.searxng_finance import search_finance_one

logger = logging.getLogger(__name__)

_SEARCH_CATEGORIES = ("news", "general")


@dataclass
class SearchAttempt:
    hits: list[dict[str, Any]] = field(default_factory=list)
    engine: str = ""
    category: str = ""
    query: str = ""
    raw_count: int = 0
    engine_failed: bool = False
    discovery_pass: str = ""
    domain_filter_exhausted: bool = False


@dataclass
class CandidateTrial:
    url: str
    hit: dict[str, Any]
    score: float
    engine: str
    query: str
    category: str = ""


@dataclass
class SearchAgentOutcome:
    record: ExternalPredictionRecord | None = None
    engines_tried: list[str] = field(default_factory=list)
    queries_run: int = 0
    candidates_tried: list[str] = field(default_factory=list)
    search_provenance: dict[str, Any] = field(default_factory=dict)


@dataclass
class CandidateHeap:
    """Max-heap of discovery candidates by relevance score."""

    _heap: list[tuple[float, int, CandidateTrial]] = field(default_factory=list)
    _seq: int = 0
    _seen_urls: set[str] = field(default_factory=set)

    def push(self, trial: CandidateTrial) -> None:
        url = trial.url.strip()
        if not url or url in self._seen_urls:
            return
        self._seen_urls.add(url)
        self._seq += 1
        heapq.heappush(self._heap, (-trial.score, self._seq, trial))

    def pop(self) -> CandidateTrial | None:
        if not self._heap:
            return None
        _neg, _seq, trial = heapq.heappop(self._heap)
        return trial

    def __len__(self) -> int:
        return len(self._heap)


def finance_engine_chain() -> list[str]:
    engines = parse_engine_list(searxng_finance_engines())
    return engines or ["duckduckgo", "bing"]


def passes_verified_quality_gates(
    record: ExternalPredictionRecord,
    source: ExternalPredictionSource,
    url: str,
    *,
    title: str = "",
    content: str = "",
) -> bool:
    if record.fetch_status != "ok":
        return False
    if (record.confidence or "").lower() == "low":
        return False
    extraction = dict(record.extraction or {})
    if not extraction.get("vision_checked"):
        return False
    host = host_from_url(url)
    if is_syndication_domain(host) and not has_stronger_attribution(
        url,
        source=source,
        title=title,
        content=content,
    ):
        return False
    return True


def _filter_trusted_with_attribution(
    rows: list[dict[str, Any]],
    source: ExternalPredictionSource,
) -> list[dict[str, Any]]:
    return [
        row
        for row in rows
        if has_stronger_attribution(
            str(row.get("url") or ""),
            source=source,
            title=str(row.get("title") or ""),
            content=str(row.get("content") or ""),
        )
    ]


def _search_query_engine(
    source: ExternalPredictionSource,
    query: str,
    *,
    engine: str,
    limit: int = 8,
    pipeline: PipelineLogger | None = None,
) -> SearchAttempt:
    domain_filter_exhausted = False
    rejected_sample: list[str] = []
    for category in _SEARCH_CATEGORIES:
        for pass_name, allowed_domains in discovery_search_passes(source):
            stats: dict[str, Any] = {}
            time_range = "day" if category == "news" else None
            rows, engine_failed, raw_count = search_finance_one(
                query,
                engine=engine,
                category=category,
                limit=limit,
                allowed_domains=allowed_domains or None,
                time_range=time_range,
                stats=stats,
            )
            rejected_sample = list(stats.get("rejected_hosts_sample") or [])
            if pass_name == "trusted" and rows:
                rows = _filter_trusted_with_attribution(rows, source)
            if rows:
                if pipeline and pass_name != "native":
                    pipeline.info(
                        "search_agent",
                        f"Discovery pass '{pass_name}' returned {len(rows)} hit(s)",
                        source_id=source.id,
                        engine=engine,
                        query=query,
                    )
                return SearchAttempt(
                    hits=rows,
                    engine=engine,
                    category=category,
                    query=query,
                    raw_count=raw_count,
                    engine_failed=engine_failed,
                    discovery_pass=pass_name,
                    domain_filter_exhausted=domain_filter_exhausted,
                )
            if raw_count > 0:
                domain_filter_exhausted = True
            elif pass_name == "native" and raw_count == 0:
                break
        if category == "news":
            continue
    return SearchAttempt(
        hits=[],
        engine=engine,
        category=_SEARCH_CATEGORIES[-1],
        query=query,
        raw_count=0,
        engine_failed=False,
        domain_filter_exhausted=domain_filter_exhausted,
    )


def _normalize_tried_url(url: str) -> str:
    return str(url or "").strip().split("#")[0].rstrip("/")


def _hit_allowed(url: str, title: str) -> bool:
    policy = is_allowed_url(url, title=title)
    if policy.allowed:
        return True
    return is_candidate_article_url(url, title=title).allowed


def _try_search_candidate(
    source: ExternalPredictionSource,
    trial: CandidateTrial,
    *,
    symbol: str,
    horizon_days: int,
    spot: float | None,
    pipeline: PipelineLogger | None = None,
) -> ExternalPredictionRecord | None:
    url = trial.url
    title = str(trial.hit.get("title") or "")
    if pipeline:
        pipeline.info(
            "search_agent",
            f"Trying candidate {url[:120]}",
            source_id=source.id,
            engine=trial.engine,
            query=trial.query,
        )
    crawled = crawl_single_url(url, pipeline=pipeline)
    if crawled is None:
        return None
    _crawl_url, crawl = crawled
    if not crawl.success:
        return None

    keywords = source_keywords(source, horizon_days=horizon_days)
    markdown = crawl.markdown or ""
    filtered = filter_markdown_for_extraction(
        markdown,
        keywords,
        horizon_days=horizon_days,
    )
    snippet = "\n".join(filtered.splitlines()[:12]) if filtered.strip() else "\n".join(
        markdown.splitlines()[:12]
    )
    body = markdown.strip() or filtered
    screenshot_b64 = None
    if isinstance(crawl.metadata, dict):
        raw = crawl.metadata.get("screenshot_b64")
        if raw:
            screenshot_b64 = str(raw)

    artifacts: ScreenshotArtifacts | None = None
    if screenshot_b64:
        try:
            artifacts = persist_screenshot_b64(
                symbol=symbol.upper(),
                source_id=source.id,
                screenshot_b64=screenshot_b64,
            )
        except Exception as exc:
            logger.debug("screenshot persist failed for %s: %s", source.id, exc)

    record = extract_forecast(
        source=source,
        horizon_days=horizon_days,
        spot=spot,
        title=crawl.title or title or source.display_name,
        url=url,
        snippet=snippet,
        body=body,
        symbol=symbol,
        screenshot_artifacts=artifacts,
        pipeline=pipeline,
    )
    record.as_of = utc_now_iso()[:10]
    record.spot_at_fetch = spot
    record.provenance = {
        **dict(record.provenance or {}),
        "url": url,
        "title": crawl.title or title,
        "fetch_method": "crawl4ai_search",
        "navigation_mode": "progressive_search",
        "search_engine": trial.engine,
        "search_query": trial.query,
        "search_category": trial.category,
    }
    return record


def progressive_search_until_forecast(
    source: ExternalPredictionSource,
    *,
    symbol: str = "NIFTY",
    horizon_days: int = 14,
    spot: float | None = None,
    tried_urls: set[str] | None = None,
    batch_registry: BatchUrlRegistry | None = None,
    pipeline: PipelineLogger | None = None,
    searxng_trigger: str | None = None,
) -> SearchAgentOutcome:
    sym = symbol.upper()
    tried = tried_urls if tried_urls is not None else set()
    context = build_horizon_context(horizon_days=horizon_days)
    as_of_date = context["today"]
    trading_dates = load_nifty_trading_dates()
    primary_queries = format_queries(source, horizon_days=horizon_days)
    fallback_queries = build_fallback_queries(
        source,
        horizon_days=horizon_days,
        as_of_date=as_of_date,
        trading_dates=trading_dates,
    )
    queries = list(dict.fromkeys([*primary_queries, *fallback_queries]))
    engines = finance_engine_chain()
    heap = CandidateHeap()
    engines_tried: list[str] = []
    candidates_tried: list[str] = []
    queries_run = 0
    last_record: ExternalPredictionRecord | None = None

    for query in queries:
        if not query.strip():
            continue
        queries_run += 1
        for engine in engines:
            if engine not in engines_tried:
                engines_tried.append(engine)
            attempt = _search_query_engine(
                source,
                query,
                engine=engine,
                pipeline=pipeline,
            )
            if pipeline:
                pipeline.info(
                    "search_agent",
                    (
                        f"engine={engine} query={query!r} raw={attempt.raw_count} "
                        f"filtered={len(attempt.hits)}"
                    ),
                    source_id=source.id,
                )
            hits = filter_discovery_hits(
                attempt.hits,
                source,
                as_of_date=as_of_date,
                trading_dates=trading_dates,
            )
            for hit in hits:
                url = str(hit.get("url") or "").strip()
                if not url:
                    continue
                norm = _normalize_tried_url(url)
                if norm in tried:
                    continue
                if batch_registry is not None and batch_registry.is_claimed_by_other(url, source.id):
                    continue
                title = str(hit.get("title") or "")
                if not _hit_allowed(url, title):
                    continue
                score = score_discovery_hit(
                    hit,
                    source,
                    as_of_date=as_of_date,
                    trading_dates=trading_dates,
                )
                heap.push(
                    CandidateTrial(
                        url=url,
                        hit=hit,
                        score=score,
                        engine=engine,
                        query=query,
                        category=attempt.category,
                    )
                )

        while heap:
            trial = heap.pop()
            if trial is None:
                break
            norm = _normalize_tried_url(trial.url)
            if norm in tried:
                continue
            tried.add(norm)
            candidates_tried.append(trial.url)
            record = _try_search_candidate(
                source,
                trial,
                symbol=sym,
                horizon_days=horizon_days,
                spot=spot,
                pipeline=pipeline,
            )
            if record is None:
                continue
            last_record = record
            if record.fetch_status == "stale":
                if pipeline:
                    pipeline.info(
                        "search_agent",
                        "Candidate stale — trying next",
                        source_id=source.id,
                        url=trial.url[:120],
                    )
                continue
            if record.fetch_status != "ok":
                continue
            title = str(trial.hit.get("title") or "")
            content = str(trial.hit.get("content") or "")
            if not passes_verified_quality_gates(
                record,
                source,
                trial.url,
                title=title,
                content=content,
            ):
                reason = "quality_gate"
                if (record.confidence or "").lower() == "low":
                    reason = "low_confidence"
                elif not (record.extraction or {}).get("vision_checked"):
                    reason = "no_vision_checked"
                elif is_syndication_domain(host_from_url(trial.url)) and not has_stronger_attribution(
                    trial.url,
                    source=source,
                    title=title,
                    content=content,
                ):
                    reason = "weak_attribution"
                if pipeline:
                    pipeline.info(
                        "search_agent",
                        f"Rejected verified extract ({reason}) — next candidate",
                        source_id=source.id,
                        url=trial.url[:120],
                    )
                continue
            if batch_registry is not None:
                batch_registry.claim(trial.url, source.id)
            prov = dict(record.provenance or {})
            prov.update(
                {
                    "engines_tried": list(engines_tried),
                    "candidates_tried": list(candidates_tried),
                    "vision_checked": True,
                    "fetch_method": "crawl4ai_search",
                    "navigation_mode": "progressive_search",
                }
            )
            if searxng_trigger:
                prov["searxng_trigger"] = searxng_trigger
                prov["searxng_attempted"] = True
            record.provenance = prov
            return SearchAgentOutcome(
                record=record,
                engines_tried=engines_tried,
                queries_run=queries_run,
                candidates_tried=candidates_tried,
                search_provenance=prov,
            )

    return SearchAgentOutcome(
        record=None,
        engines_tried=engines_tried,
        queries_run=queries_run,
        candidates_tried=candidates_tried,
    )
