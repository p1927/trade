"""Bounded exploratory browse loop for external prediction sources (max 8 steps)."""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable
from urllib.parse import urlparse

from trade_integrations.dataflows.crawl4ai_client import CrawlPageResult, crawl_urls_parallel_sync
from trade_integrations.dataflows.index_research.external_predictions.crawl4ai_fetcher import (
    extract_article_links,
    keyword_match_score,
    source_keywords,
)
from trade_integrations.dataflows.index_research.external_predictions.models import (
    ExternalPredictionSource,
    NavigationStep,
    NavigationTrace,
    utc_now_iso,
)
from trade_integrations.dataflows.index_research.external_predictions.url_policy import (
    classify_page_kind,
    is_allowed_listing_url,
    link_has_forecast_signal,
    link_score,
    markdown_has_nifty50_forecast,
)
from trade_integrations.dataflows.index_research.pipeline_log import PipelineLogger

logger = logging.getLogger(__name__)

MAX_BROWSE_STEPS = 8
MAX_VISION_CALLS_PER_BROWSE_STEP = 2
_MD_LINK = re.compile(r"\[([^\]]*)\]\((https?://[^)]+)\)", re.I)
CrawlOneFn = Callable[[str, bool], CrawlPageResult]


def _format_entry_url(url: str, *, horizon_days: int) -> str:
    year = str(datetime.now(timezone.utc).year)
    return url.replace("{horizon}", str(horizon_days)).replace("{year}", year)


@dataclass
class BrowseResult:
    success: bool
    trace: NavigationTrace
    url: str = ""
    title: str = ""
    markdown: str = ""
    elapsed_ms: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)
    error_message: str = ""
    steps_taken: int = 0


def has_browse_entry_urls(source: ExternalPredictionSource) -> bool:
    """True when the source has user-configured entry URLs for exploratory browse."""
    return any(str(url or "").strip() for url in (source.entry_urls or []))


def resolve_browse_entry_urls(
    source: ExternalPredictionSource,
    *,
    horizon_days: int = 14,
    include_landing_fallback: bool = False,
) -> list[str]:
    """Return allowlisted, formatted entry URLs for exploratory browse."""
    urls: list[str] = []
    seen: set[str] = set()
    raw_entries = list(source.entry_urls or [])
    if not raw_entries and include_landing_fallback:
        raw_entries = list(source.landing_urls or [])[:1]
    for raw in raw_entries:
        url = _format_entry_url(str(raw or "").strip(), horizon_days=horizon_days)
        if not url or url in seen:
            continue
        policy = is_allowed_listing_url(url)
        if not policy.allowed:
            continue
        seen.add(url)
        urls.append(url)
    return urls


def browse_result_to_crawl_row(result: BrowseResult) -> tuple[str, CrawlPageResult]:
    """Convert browse output to refresh pipeline crawl row shape."""
    return (
        result.url,
        CrawlPageResult(
            url=result.url,
            success=result.success,
            title=result.title,
            markdown=result.markdown,
            elapsed_ms=result.elapsed_ms,
            metadata=dict(result.metadata or {}),
            error_message=result.error_message,
        ),
    )


def _default_crawl_one(url: str, score_links: bool) -> CrawlPageResult:
    rows = crawl_urls_parallel_sync([url], score_links=score_links, capture_screenshot=True)
    if not rows:
        return CrawlPageResult(url=url, success=False, error_message="browse_crawl_empty")
    return rows[0]


def _row_screenshot_b64(row: CrawlPageResult) -> str:
    if isinstance(row.metadata, dict):
        return str(row.metadata.get("screenshot_b64") or "")
    return ""


def _detect_row_blocked(row: CrawlPageResult, *, url: str) -> Any:
    from trade_integrations.dataflows.index_research.external_predictions.page_block_detector import (
        detect_blocked_page,
    )

    return detect_blocked_page(
        url=url,
        markdown=row.markdown or "",
        screenshot_b64=_row_screenshot_b64(row) or None,
        title=row.title or "",
    )


def _try_vision_blocked_recovery(
    row: CrawlPageResult,
    *,
    url: str,
    pipeline: PipelineLogger | None,
) -> CrawlPageResult:
    """Run vision overlay recovery when crawl still looks blocked."""
    from trade_integrations.dataflows.crawl4ai_client import vision_nav_enabled, vision_navigate_url_sync

    if not vision_nav_enabled():
        return row
    block_signal = _detect_row_blocked(row, url=url)
    if not block_signal.blocked:
        return row
    if pipeline:
        pipeline.info(
            "vision_nav",
            f"Browse step blocked ({', '.join(block_signal.reasons)}) — vision recovery",
            url=url[:120],
        )
    try:
        recovered = vision_navigate_url_sync(url, pipeline=pipeline)
    except Exception as exc:
        if pipeline:
            pipeline.warn("vision_nav", f"Browse vision recovery failed: {exc}", url=url[:120])
        logger.debug("browse vision recovery failed for %s", url, exc_info=True)
        return row
    if recovered.success:
        return recovered
    return row


def _collect_browse_link_candidates(
    *,
    markdown: str,
    native_links: list[dict[str, Any]] | None,
    source: ExternalPredictionSource,
    visited: set[str],
    current_url: str,
) -> list[tuple[str, str]]:
    candidates: list[tuple[str, str]] = []
    for title, url, _ in _iter_browse_link_candidates(markdown, native_links):
        norm = _normalize_url(url)
        if norm in visited or url == current_url:
            continue
        if not _domain_matches_source(url, source):
            continue
        if not link_has_forecast_signal(f"{title} {url}"):
            continue
        candidates.append((title or url, url))
        if len(candidates) >= 10:
            break
    return candidates


def _vision_pick_listing_link(
    *,
    markdown: str,
    native_links: list[dict[str, Any]] | None,
    source: ExternalPredictionSource,
    screenshot_b64: str,
    visited: set[str],
    current_url: str,
    pipeline: PipelineLogger | None = None,
    goal: str = "pick_listing_link",
) -> tuple[str, str]:
    """Vision-assisted link pick when listing markdown is thin or link extraction failed."""
    from trade_integrations.dataflows.crawl4ai_client import vision_nav_enabled
    from trade_integrations.dataflows.index_research.external_predictions.vision_navigator import (
        BrowseVisionGoal,
        plan_vision_browse_next_url,
    )

    if not vision_nav_enabled():
        return "", ""
    if goal == "pick_listing_link" and len((markdown or "").strip()) >= 1200:
        return "", ""

    candidates = _collect_browse_link_candidates(
        markdown=markdown,
        native_links=native_links,
        source=source,
        visited=visited,
        current_url=current_url,
    )
    browse_goal: BrowseVisionGoal = (
        "open_forecast_article" if goal == "open_forecast_article" else "pick_listing_link"
    )
    if browse_goal == "pick_listing_link" and not candidates:
        return "", ""

    try:
        plan = plan_vision_browse_next_url(
            screenshot_b64=screenshot_b64,
            url=current_url,
            goal=browse_goal,
            candidates=candidates or None,
        )
    except Exception as exc:
        if pipeline:
            pipeline.warn("browse", f"Vision link pick skipped: {exc}", source_id=source.id)
        return "", ""

    next_url = str(plan.next_url or "").strip()
    if not next_url:
        return "", ""
    norm = _normalize_url(next_url)
    if norm in visited or next_url == current_url:
        return "", ""
    if not _domain_matches_source(next_url, source):
        return "", ""

    link_title = ""
    for title, url, _ in _iter_browse_link_candidates(markdown, native_links):
        if url == next_url and title:
            link_title = title
            break
    if pipeline:
        pipeline.info(
            "vision_nav",
            f"Vision browse pick ({browse_goal})",
            source_id=source.id,
            url=next_url[:120],
        )
    return next_url, link_title


def _vision_pick_next_url_for_step(
    *,
    row: CrawlPageResult,
    markdown: str,
    native_links: list[dict[str, Any]] | None,
    source: ExternalPredictionSource,
    current_url: str,
    visited: set[str],
    pipeline: PipelineLogger | None,
    vision_calls: int,
) -> tuple[str, str, int]:
    """Try up to remaining vision budget to pick next browse URL."""
    screenshot_b64 = _row_screenshot_b64(row)
    if not screenshot_b64:
        return "", "", vision_calls

    remaining = MAX_VISION_CALLS_PER_BROWSE_STEP - vision_calls
    if remaining <= 0:
        return "", "", vision_calls

    next_url, link_text = _vision_pick_listing_link(
        markdown=markdown,
        native_links=native_links,
        source=source,
        screenshot_b64=screenshot_b64,
        visited=visited,
        current_url=current_url,
        pipeline=pipeline,
        goal="open_forecast_article",
    )
    vision_calls += 1
    if next_url or remaining <= 1:
        return next_url, link_text, vision_calls

    pick_url, pick_title = _vision_pick_listing_link(
        markdown=markdown,
        native_links=native_links,
        source=source,
        screenshot_b64=screenshot_b64,
        visited=visited,
        current_url=current_url,
        pipeline=pipeline,
        goal="pick_listing_link",
    )
    vision_calls += 1
    if pick_url:
        return pick_url, pick_title or link_text, vision_calls
    return next_url, link_text, vision_calls


def _domain_matches_source(url: str, source: ExternalPredictionSource) -> bool:
    host = (urlparse(url).hostname or "").lower().removeprefix("www.")
    if not host:
        return False
    for domain in source.domains:
        d = domain.lower().removeprefix("www.")
        if host == d or host.endswith(f".{d}"):
            return True
    return False


def _iter_browse_link_candidates(
    markdown: str,
    native_links: list[dict[str, Any]] | None,
) -> list[tuple[str, str, float | None]]:
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


def _pick_next_url(
    *,
    markdown: str,
    native_links: list[dict[str, Any]] | None,
    source: ExternalPredictionSource,
    current_url: str,
    visited: set[str],
) -> tuple[str, str]:
    """Return (next_url, link_title) for the highest-ranked unvisited browse candidate."""
    candidates = extract_article_links(
        markdown,
        source,
        native_links=native_links,
        pipeline=None,
    )
    for candidate in candidates:
        norm = _normalize_url(candidate)
        if norm in visited or candidate == current_url:
            continue
        link_title = ""
        for title, url, _ in _iter_browse_link_candidates(markdown, native_links):
            if url == candidate and title:
                link_title = title
                break
        return candidate, link_title

    scored: list[tuple[float, str, str]] = []
    for title, url, native_score in _iter_browse_link_candidates(markdown, native_links):
        norm = _normalize_url(url)
        if norm in visited or url == current_url:
            continue
        if not _domain_matches_source(url, source):
            continue
        if not is_allowed_listing_url(url).allowed:
            continue
        if not link_has_forecast_signal(f"{title} {url}"):
            continue
        scored.append((link_score(title, url, native_score=native_score), url, title))
    scored.sort(key=lambda item: item[0], reverse=True)
    if scored:
        return scored[0][1], scored[0][2]
    return "", ""


def _page_has_forecast(
    markdown: str,
    *,
    url: str,
    horizon_days: int,
    keywords: list[str],
) -> bool:
    """True when page body contains a NIFTY 50 forecast signal."""
    del url  # qualify on content, not URL shape
    if not markdown_has_nifty50_forecast(markdown):
        return False
    return keyword_match_score(markdown, keywords, horizon_days=horizon_days) >= 1.0


def _normalize_url(url: str) -> str:
    parsed = urlparse(url.strip())
    path = parsed.path.rstrip("/") or "/"
    return f"{parsed.scheme}://{parsed.netloc.lower()}{path}"


def run_exploratory_browse(
    source: ExternalPredictionSource,
    *,
    horizon_days: int = 14,
    pipeline: PipelineLogger | None = None,
    crawl_one: CrawlOneFn | None = None,
    max_steps: int | None = None,
    include_landing_fallback: bool = False,
) -> BrowseResult:
    """
    Bounded browse loop starting from ``entry_urls``.

    Each step crawls one URL via Crawl4AI (Playwright). When the page body contains
    a NIFTY 50 forecast signal, browsing stops early. Returns a ``NavigationTrace``
    compatible with path auto-save and refresh extraction.
    """
    step_limit = max(1, min(int(max_steps or MAX_BROWSE_STEPS), MAX_BROWSE_STEPS))
    entry_urls = resolve_browse_entry_urls(
        source,
        horizon_days=horizon_days,
        include_landing_fallback=include_landing_fallback,
    )
    empty_trace = NavigationTrace(steps=[], final_url="", created_at=utc_now_iso())
    if not entry_urls:
        return BrowseResult(
            success=False,
            trace=empty_trace,
            error_message="no_entry_urls",
        )

    crawl_fn = crawl_one or _default_crawl_one
    keywords = source_keywords(source, horizon_days=horizon_days)
    steps: list[NavigationStep] = []
    visited: set[str] = set()
    current_url = entry_urls[0]
    total_elapsed_ms = 0.0
    last_row: CrawlPageResult | None = None
    last_url = ""
    found_forecast = False

    if pipeline:
        pipeline.info(
            "browse",
            f"Exploratory browse for {source.display_name} (max {step_limit} steps)",
            source_id=source.id,
            entry_url=current_url[:120],
        )

    try:
        from trade_integrations.observability.loop_guard import LoopGuard
    except ImportError:
        LoopGuard = None  # type: ignore[misc, assignment]

    loop = (
        LoopGuard(
            f"browse:{source.id}",
            module="ingest",
            max_iterations=step_limit,
            warn_every=0,
        )
        if LoopGuard is not None
        else None
    )

    step_idx = 0
    while loop is None or loop.tick():
        step_idx += 1
        if loop is None and step_idx > step_limit:
            break
        norm = _normalize_url(current_url)
        if norm in visited:
            break
        visited.add(norm)

        score_links = step_idx == 1
        row = crawl_fn(current_url, score_links)
        total_elapsed_ms += float(row.elapsed_ms or 0.0)
        last_row = row
        last_url = current_url

        action: str = "goto" if step_idx == 1 else "click"
        steps.append(
            NavigationStep(
                action=action,  # type: ignore[arg-type]
                url=current_url,
            )
        )

        if not row.success:
            if pipeline:
                pipeline.warn(
                    "browse",
                    f"Step {step_idx} crawl failed: {row.error_message or 'unknown'}",
                    source_id=source.id,
                    url=current_url[:120],
                )
            break

        vision_calls = 0
        block_signal = _detect_row_blocked(row, url=current_url)
        overlay_blocked = block_signal.blocked and any(
            token in reason
            for reason in block_signal.reasons
            for token in ("cookie", "notification", "overlay", "bot")
        )
        if overlay_blocked and vision_calls < MAX_VISION_CALLS_PER_BROWSE_STEP:
            recovered = _try_vision_blocked_recovery(row, url=current_url, pipeline=pipeline)
            if recovered is not row:
                row = recovered
                last_row = row
                total_elapsed_ms += float(row.elapsed_ms or 0.0)
                vision_calls += 1

        if _page_has_forecast(
            row.markdown,
            url=current_url,
            horizon_days=horizon_days,
            keywords=keywords,
        ):
            found_forecast = True
            if pipeline:
                pipeline.info(
                    "browse",
                    f"Forecast page found at step {step_idx}",
                    source_id=source.id,
                    url=current_url[:120],
                )
            break

        native_links = row.metadata.get("links") if isinstance(row.metadata, dict) else None
        next_url, link_text = _pick_next_url(
            markdown=row.markdown,
            native_links=native_links if isinstance(native_links, list) else None,
            source=source,
            current_url=current_url,
            visited=visited,
        )
        if not next_url:
            next_url, link_text, vision_calls = _vision_pick_next_url_for_step(
                row=row,
                markdown=row.markdown,
                native_links=native_links if isinstance(native_links, list) else None,
                source=source,
                current_url=current_url,
                visited=visited,
                pipeline=pipeline,
                vision_calls=vision_calls,
            )
        if not next_url:
            if pipeline:
                pipeline.info(
                    "browse",
                    f"No further article links at step {step_idx}",
                    source_id=source.id,
                    url=current_url[:120],
                )
            break

        if link_text:
            steps[-1].text = link_text
        current_url = next_url

    trace = NavigationTrace(
        steps=steps,
        final_url=last_url,
        approved_by="auto",
        stale=False,
        created_at=utc_now_iso(),
    )

    if last_row is None or not last_row.success:
        return BrowseResult(
            success=False,
            trace=trace,
            url=last_url,
            error_message=(last_row.error_message if last_row else "browse_no_pages"),
            steps_taken=len(steps),
        )

    has_forecast = found_forecast or _page_has_forecast(
        last_row.markdown,
        url=last_url,
        horizon_days=horizon_days,
        keywords=keywords,
    )
    page_kind = classify_page_kind(last_url)
    success = has_forecast and page_kind in {"article", "hub"}
    return BrowseResult(
        success=success,
        trace=trace,
        url=last_url,
        title=last_row.title,
        markdown=last_row.markdown,
        elapsed_ms=total_elapsed_ms,
        metadata=dict(last_row.metadata or {}),
        error_message="" if success else "browse_no_forecast",
        steps_taken=len(steps),
    )


def browse_enabled_for_source(source: ExternalPredictionSource) -> bool:
    """Gate exploratory browse on entry URLs and optional env disable."""
    if os.environ.get("EXTERNAL_PREDICTIONS_BROWSE_DISABLED", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }:
        return False
    return has_browse_entry_urls(source)
