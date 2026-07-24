"""SearXNG search and article fetch for external predictions."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from email.utils import parsedate_to_datetime
from typing import Any
from urllib.parse import urlparse

from trade_integrations.dataflows.article_body import fetch_article_body
from trade_integrations.dataflows.index_research.external_predictions.domain_utils import (
    attribution_name_tokens,
    has_stronger_attribution,
    native_domains,
    normalize_domain,
    url_matches_bank_topic,
)
from trade_integrations.dataflows.index_research.external_predictions.models import (
    ExternalPredictionRecord,
    ExternalPredictionSource,
)
from trade_integrations.dataflows.index_research.external_predictions.query_builder import (
    build_fallback_queries,
    build_horizon_context,
    load_nifty_trading_dates,
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
from trade_integrations.dataflows.searxng_finance import TRUSTED_FINANCE_DOMAINS, search_finance

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
    discovery_pass: str = ""

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


def _all_source_domains(source: ExternalPredictionSource) -> tuple[str, ...]:
    out: list[str] = []
    for domain in source.domains or []:
        norm = normalize_domain(domain)
        if norm:
            out.append(norm)
    return tuple(dict.fromkeys(out))


def _source_allowed_domains(source: ExternalPredictionSource) -> tuple[str, ...]:
    """Legacy single-pass allowlist — prefer ``discovery_search_passes``."""
    native = native_domains(source)
    if native:
        return native
    return _all_source_domains(source)


def discovery_search_passes(
    source: ExternalPredictionSource,
) -> list[tuple[str, tuple[str, ...]]]:
    """Progressive domain widening: native → all configured → trusted+attribution."""
    native = native_domains(source)
    all_domains = _all_source_domains(source)
    passes: list[tuple[str, tuple[str, ...]]] = []
    if native:
        passes.append(("native", native))
    if all_domains and all_domains != native:
        passes.append(("wide", all_domains))
    if source.kind in {"broker", "global_bank"}:
        passes.append(("trusted", TRUSTED_FINANCE_DOMAINS))
    elif not passes and all_domains:
        passes.append(("wide", all_domains))
    return passes


def _parse_pub_date_from_result(result: dict[str, Any]) -> date | None:
    for key in ("publishedDate", "pubdate"):
        raw = result.get(key)
        if not raw:
            continue
        try:
            if isinstance(raw, (int, float)):
                return datetime.fromtimestamp(raw).date()
            if isinstance(raw, str):
                text = raw.strip()
                if not text:
                    continue
                try:
                    dt = parsedate_to_datetime(text)
                except ValueError:
                    dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
                return dt.date()
        except (ValueError, TypeError, OverflowError):
            continue
    return None


def _recency_window_dates(
    as_of_date: str,
    trading_dates: list[str],
    *,
    max_trading_days: int = 3,
) -> set[str]:
    as_of = str(as_of_date)[:10]
    ordered = [str(d).strip()[:10] for d in trading_dates if str(d).strip()]
    if ordered:
        eligible = [d for d in ordered if d <= as_of]
        if not eligible:
            return {as_of}
        window = eligible[-max(1, int(max_trading_days)) :]
        return set(window)
    try:
        end = date.fromisoformat(as_of)
    except ValueError:
        return {as_of}
    return {(end - timedelta(days=offset)).isoformat() for offset in range(max_trading_days)}


def filter_results_by_recency(
    hits: list[dict[str, Any]],
    *,
    as_of_date: str,
    trading_dates: list[str] | None = None,
    max_trading_days: int = 3,
) -> list[dict[str, Any]]:
    """Keep hits published within the last N trading days; retain undated hits at the tail."""
    dates = trading_dates if trading_dates is not None else load_nifty_trading_dates()
    window = _recency_window_dates(as_of_date, dates, max_trading_days=max_trading_days)
    in_window: list[dict[str, Any]] = []
    undated: list[dict[str, Any]] = []
    for row in hits:
        pub = _parse_pub_date_from_result(row)
        if pub is None:
            undated.append(row)
            continue
        if pub.isoformat() in window:
            in_window.append(row)
    return in_window + undated


def _recency_score_adjustment(
    result: dict[str, Any],
    *,
    as_of_date: str,
    trading_dates: list[str],
) -> float:
    pub = _parse_pub_date_from_result(result)
    if pub is None:
        return 0.0
    as_of = str(as_of_date)[:10]
    if pub.isoformat() == as_of:
        return 3.0
    window = _recency_window_dates(as_of, trading_dates, max_trading_days=3)
    if pub.isoformat() in window:
        return 1.0
    return -2.0


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


def _relevance_score(
    result: dict[str, Any],
    source: ExternalPredictionSource,
    *,
    as_of_date: str | None = None,
    trading_dates: list[str] | None = None,
) -> float:
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
    if as_of_date:
        dates = trading_dates if trading_dates is not None else load_nifty_trading_dates()
        score += _recency_score_adjustment(result, as_of_date=as_of_date, trading_dates=dates)
    return score


def filter_searxng_hits_for_source(
    hits: list[dict[str, Any]],
    source: ExternalPredictionSource,
    *,
    as_of_date: str | None = None,
    trading_dates: list[str] | None = None,
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
    return sorted(
        pool,
        key=lambda row: _relevance_score(
            row,
            source,
            as_of_date=as_of_date,
            trading_dates=trading_dates,
        ),
        reverse=True,
    )


def score_discovery_hit(
    result: dict[str, Any],
    source: ExternalPredictionSource,
    *,
    as_of_date: str | None = None,
    trading_dates: list[str] | None = None,
) -> float:
    """Public relevance score for progressive search ranking."""
    return _relevance_score(
        result,
        source,
        as_of_date=as_of_date,
        trading_dates=trading_dates,
    )


def filter_discovery_hits(
    hits: list[dict[str, Any]],
    source: ExternalPredictionSource,
    *,
    as_of_date: str | None = None,
    trading_dates: list[str] | None = None,
) -> list[dict[str, Any]]:
    """Recency + domain filter for discovery hits."""
    dated = filter_results_by_recency(
        hits,
        as_of_date=as_of_date or build_horizon_context(horizon_days=14)["today"],
        trading_dates=trading_dates,
    )
    return filter_searxng_hits_for_source(
        dated,
        source,
        as_of_date=as_of_date,
        trading_dates=trading_dates,
    )


def rank_search_results(
    results: list[dict[str, Any]],
    source: ExternalPredictionSource,
    *,
    limit: int | None = None,
    as_of_date: str | None = None,
    trading_dates: list[str] | None = None,
) -> list[dict[str, Any]]:
    cap = limit if limit is not None else _discovery_url_limit()
    filtered = filter_searxng_hits_for_source(
        results,
        source,
        as_of_date=as_of_date,
        trading_dates=trading_dates,
    )
    ranked = sorted(
        filtered,
        key=lambda row: _relevance_score(
            row,
            source,
            as_of_date=as_of_date,
            trading_dates=trading_dates,
        ),
        reverse=True,
    )
    return ranked[:cap]


def _search_query_with_passes(
    source: ExternalPredictionSource,
    query: str,
    *,
    limit: int,
    pipeline: PipelineLogger | None = None,
) -> tuple[list[dict[str, Any]], bool, str, list[str]]:
    """Run one query with progressive domain widening; return rows, exhausted flag, pass name."""
    domain_filter_exhausted = False
    rejected_sample: list[str] = []
    for pass_name, allowed_domains in discovery_search_passes(source):
        query_stats: dict[str, Any] = {}
        rows = search_finance(
            query,
            limit=limit,
            allowed_domains=allowed_domains or None,
            time_range="day",
            stats=query_stats,
        )
        raw_count = int(query_stats.get("raw_count") or 0)
        rejected_sample = list(query_stats.get("rejected_hosts_sample") or [])
        if pass_name == "trusted" and rows:
            rows = _filter_trusted_with_attribution(rows, source)
        if rows:
            if pipeline and pass_name != "native":
                pipeline.info(
                    "searxng",
                    f"Discovery pass '{pass_name}' returned {len(rows)} hit(s)",
                    source_id=source.id,
                    query=query,
                )
            return rows, domain_filter_exhausted, pass_name, rejected_sample
        if raw_count > 0:
            domain_filter_exhausted = True
        elif pass_name == "native":
            # No raw hits at all — widening allowlist won't help; skip to next query.
            break
    return [], domain_filter_exhausted, "", rejected_sample


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


def search_source_results_with_outcome(
    source: ExternalPredictionSource,
    *,
    horizon_days: int,
    limit: int = 8,
    pipeline: PipelineLogger | None = None,
) -> SearxngSearchOutcome:
    collected: list[dict[str, Any]] = []
    seen_urls: set[str] = set()
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
    queries_run = 0
    queries_failed = 0
    domain_filter_exhausted = False
    discovery_pass = ""
    passes = discovery_search_passes(source)
    if not passes:
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

    def _run_query_batch(query_list: list[str], *, label: str) -> None:
        nonlocal queries_run, queries_failed, domain_filter_exhausted, discovery_pass
        if not query_list:
            return
        if pipeline and label == "fallback":
            pipeline.info(
                "searxng",
                f"Running {len(query_list)} fallback quer{'y' if len(query_list) == 1 else 'ies'} for {source.display_name}",
                source_id=source.id,
            )
        for query in query_list:
            queries_run += 1
            if pipeline:
                pipeline.info("searxng", f"Query: {query}", source_id=source.id)
            try:
                rows, exhausted, pass_name, rejected_sample = _search_query_with_passes(
                    source,
                    query,
                    limit=limit,
                    pipeline=pipeline,
                )
            except Exception as exc:
                queries_failed += 1
                if pipeline:
                    pipeline.warn("searxng", f"Search failed: {exc}", source_id=source.id, query=query)
                logger.debug("search failed for %s query=%r: %s", source.id, query, exc)
                continue
            if exhausted:
                domain_filter_exhausted = True
            if pipeline:
                raw_hint = ""
                if not rows and rejected_sample:
                    raw_hint = f" — sample hosts: {', '.join(rejected_sample[:3])}"
                if not rows and exhausted:
                    msg = f"Got 0 result(s) after domain filter{raw_hint}"
                else:
                    msg = f"Got {len(rows)} result(s) for query"
                    if pass_name:
                        msg += f" (pass={pass_name})"
                pipeline.info(
                    "searxng",
                    msg,
                    source_id=source.id,
                    query=query,
                )
            if pass_name and not discovery_pass:
                discovery_pass = pass_name
            rows = filter_results_by_recency(
                rows,
                as_of_date=as_of_date,
                trading_dates=trading_dates,
            )
            for row in rows:
                url = str(row.get("url") or "")
                if not url or url in seen_urls:
                    continue
                seen_urls.add(url)
                collected.append(row)

    if pipeline:
        pipeline.info(
            "searxng",
            f"Searching {source.display_name} ({len(primary_queries)} queries)",
            source_id=source.id,
        )
    _run_query_batch(primary_queries, label="primary")
    if not collected and fallback_queries:
        _run_query_batch(fallback_queries, label="fallback")

    ranked = rank_search_results(
        collected,
        source,
        as_of_date=as_of_date,
        trading_dates=trading_dates,
    )
    outcome = SearxngSearchOutcome(
        hits=ranked,
        queries_run=queries_run,
        queries_failed=queries_failed,
        domain_filter_exhausted=domain_filter_exhausted,
        discovery_pass=discovery_pass,
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
                discovery_pass=discovery_pass or None,
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


def probe_searxng_for_source(
    source: ExternalPredictionSource,
    *,
    horizon_days: int = 14,
) -> dict[str, Any]:
    """Diagnostic: compare SearXNG hits across domain passes for each query."""
    from trade_integrations.dataflows.searxng_finance import search_finance as raw_search

    primary_queries = format_queries(source, horizon_days=horizon_days)
    fallback_queries = build_fallback_queries(source, horizon_days=horizon_days)
    report: dict[str, Any] = {
        "source_id": source.id,
        "display_name": source.display_name,
        "kind": source.kind,
        "domains": list(source.domains or []),
        "search_queries": list(source.search_queries or []),
        "primary_queries": primary_queries,
        "fallback_queries": fallback_queries,
        "passes": [name for name, _ in discovery_search_passes(source)],
        "query_results": [],
    }
    for query in (primary_queries[:3] or primary_queries):
        entry: dict[str, Any] = {"query": query, "passes": []}
        for pass_name, allowed_domains in discovery_search_passes(source):
            stats: dict[str, Any] = {}
            try:
                rows = raw_search(
                    query,
                    limit=8,
                    allowed_domains=allowed_domains or None,
                    time_range="day",
                    stats=stats,
                )
                if pass_name == "trusted" and rows:
                    rows = _filter_trusted_with_attribution(rows, source)
            except Exception as exc:
                entry["passes"].append(
                    {
                        "pass": pass_name,
                        "allowed_domains": list(allowed_domains),
                        "error": str(exc),
                    }
                )
                continue
            entry["passes"].append(
                {
                    "pass": pass_name,
                    "allowed_domains": list(allowed_domains),
                    "raw_count": stats.get("raw_count", len(rows)),
                    "accepted_count": len(rows),
                    "rejected_hosts_sample": stats.get("rejected_hosts_sample") or [],
                    "accepted_hosts": [
                        _domain(urlparse(str(row.get("url") or "")).hostname or "")
                        for row in rows[:5]
                    ],
                    "sample_titles": [str(row.get("title") or "")[:80] for row in rows[:3]],
                }
            )
        report["query_results"].append(entry)
    outcome = search_source_results_with_outcome(source, horizon_days=horizon_days)
    report["outcome"] = {
        "ranked_hits": len(outcome.hits),
        "queries_run": outcome.queries_run,
        "domain_filter_exhausted": outcome.domain_filter_exhausted,
        "discovery_pass": outcome.discovery_pass,
    }
    return report


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
    max_workers = min(2, len(sources))
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
        url = str(hit.get("url") or "").strip()
        title = str(hit.get("title") or "")
        if not url:
            continue
        from trade_integrations.dataflows.index_research.external_predictions.crawl4ai_fetcher import (
            crawl_single_url,
        )
        from trade_integrations.dataflows.index_research.external_predictions.screenshot_utils import (
            persist_screenshot_b64,
        )

        crawled = crawl_single_url(url, pipeline=pipeline)
        if crawled is None:
            continue
        _u, crawl = crawled
        if not crawl.success or len((crawl.markdown or "").strip()) < 80:
            continue
        markdown = crawl.markdown or ""
        filtered = filter_markdown_for_extraction(
            markdown,
            keywords,
            horizon_days=horizon_days,
        )
        snippet = "\n".join(filtered.splitlines()[:12])
        body = markdown.strip() or filtered
        screenshot_b64 = None
        if isinstance(crawl.metadata, dict):
            raw = crawl.metadata.get("screenshot_b64")
            if raw:
                screenshot_b64 = str(raw)
        artifacts = None
        if screenshot_b64:
            artifacts = persist_screenshot_b64(
                symbol=sym,
                source_id=source.id,
                screenshot_b64=screenshot_b64,
            )
        record = extract_forecast(
            source=source,
            horizon_days=horizon_days,
            spot=spot,
            title=crawl.title or title or source.display_name,
            url=url,
            snippet=snippet,
            body=body,
            symbol=sym,
            screenshot_artifacts=artifacts,
            pipeline=pipeline,
        )
        record.as_of = utc_now_iso()[:10]
        record.spot_at_fetch = spot
        record.provenance = {
            **dict(record.provenance or {}),
            "url": url,
            "title": crawl.title or title or record.provenance.get("title", ""),
            "fetch_method": "crawl4ai_search",
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
        if record.fetch_status == "stale":
            if pipeline:
                pipeline.info(
                    "searxng",
                    f"Article outside recency window ({record.published_at or 'unknown'}) — trying next hit",
                    source_id=source.id,
                    url=url[:120],
                )
            continue

    return last_record
