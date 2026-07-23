"""Fast-path replay and exploratory fallback for external prediction sources."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from trade_integrations.dataflows.crawl4ai_client import CrawlPageResult, crawl_urls_parallel_sync
from trade_integrations.dataflows.index_research.external_predictions.models import (
    ExternalPredictionSource,
    NavigationStep,
    NavigationTrace,
)
from trade_integrations.dataflows.index_research.external_predictions.path_store import (
    get_effective_path,
    mark_path_stale,
    save_auto_path,
)
from trade_integrations.dataflows.index_research.external_predictions.url_policy import (
    is_allowed_listing_url,
)
from trade_integrations.dataflows.index_research.pipeline_log import PipelineLogger

logger = logging.getLogger(__name__)


@dataclass
class ReplayResult:
    success: bool
    url: str = ""
    title: str = ""
    markdown: str = ""
    elapsed_ms: int = 0
    mode: str = "exploratory"
    error_message: str = ""
    metadata: dict = field(default_factory=dict)


def resolve_replay_url(trace: NavigationTrace) -> str:
    if trace.final_url.strip():
        return trace.final_url.strip()
    for step in reversed(trace.steps):
        if step.url.strip():
            return step.url.strip()
    return ""


def replay_navigation_path(
    trace: NavigationTrace,
    *,
    source: ExternalPredictionSource,
    pipeline: PipelineLogger | None = None,
) -> ReplayResult:
    """Replay a saved path. Phase 2 skeleton: direct crawl of final URL."""
    if trace.stale:
        return ReplayResult(success=False, mode="fast", error_message="path_stale")
    url = resolve_replay_url(trace)
    if not url:
        return ReplayResult(success=False, mode="fast", error_message="path_missing_url")

    policy = is_allowed_listing_url(url)
    if not policy.allowed:
        return ReplayResult(success=False, mode="fast", error_message=policy.reason)

    if pipeline:
        pipeline.info(
            "navigation",
            f"Fast-path replay for {source.display_name}",
            source_id=source.id,
            url=url[:120],
            steps=len(trace.steps),
        )

    try:
        results = crawl_urls_parallel_sync([url], pipeline=pipeline)
    except Exception as exc:
        logger.debug("replay crawl failed for %s: %s", source.id, exc)
        return ReplayResult(success=False, mode="fast", error_message=str(exc))

    if not results:
        return ReplayResult(success=False, mode="fast", error_message="replay_empty")

    row = results[0]
    if not row.success or not (row.markdown or "").strip():
        return ReplayResult(
            success=False,
            mode="fast",
            error_message=row.error_message or "replay_crawl_failed",
        )

    return ReplayResult(
        success=True,
        url=url,
        title=row.title or source.display_name,
        markdown=row.markdown,
        elapsed_ms=row.elapsed_ms,
        mode="fast",
        metadata=dict(row.metadata or {}),
    )


def try_fast_path_then_exploratory(
    source: ExternalPredictionSource,
    *,
    horizon_days: int,
    exploratory_rows: list[tuple[str, CrawlPageResult]],
    pipeline: PipelineLogger | None = None,
) -> tuple[
    ReplayResult | None,
    list[tuple[str, CrawlPageResult]],
    list[tuple[str, CrawlPageResult]],
]:
    """
    Attempt fast-path replay when a saved path exists.

    Returns ``(replay_result, active_rows, exploratory_backup)``. On replay
    success, ``active_rows`` is the synthetic replay page but ``exploratory_backup``
    is preserved so callers can fall back if extraction fails. Does not touch path
    success until the caller confirms an ok extract.
    """
    exploratory_backup = list(exploratory_rows)
    trace = get_effective_path(source, horizon_days=horizon_days)
    if trace is None:
        return None, exploratory_backup, exploratory_backup

    replay = replay_navigation_path(trace, source=source, pipeline=pipeline)
    if replay.success:
        synthetic = CrawlPageResult(
            url=replay.url,
            success=True,
            title=replay.title,
            markdown=replay.markdown,
            elapsed_ms=float(replay.elapsed_ms),
            metadata=dict(replay.metadata or {}),
        )
        return replay, [(replay.url, synthetic)], exploratory_backup

    if pipeline:
        pipeline.warn(
            "navigation",
            f"Fast-path replay failed — falling back to exploratory ({replay.error_message})",
            source_id=source.id,
        )
    mark_path_stale(source.id, horizon_days=horizon_days)
    return replay, exploratory_backup, exploratory_backup


def persist_successful_exploratory_path(
    source_id: str,
    *,
    horizon_days: int,
    url: str,
    steps: list[NavigationStep] | None = None,
    pipeline: PipelineLogger | None = None,
) -> NavigationTrace | None:
    trace = save_auto_path(
        source_id,
        horizon_days=horizon_days,
        final_url=url,
        steps=steps,
    )
    if trace and pipeline:
        pipeline.info(
            "navigation",
            "Auto-saved exploratory navigation path",
            source_id=source_id,
            url=url[:120],
            horizon_days=horizon_days,
        )
    return trace
