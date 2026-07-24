"""Batch refresh of watchlisted external prediction sources."""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from trade_integrations.dataflows.crawl4ai_client import crawl4ai_queue_stats
from trade_integrations.dataflows.index_research.external_predictions.batch_url_dedup import (
    BatchUrlRegistry,
)
from trade_integrations.dataflows.index_research.external_predictions.crawl4ai_fetcher import (
    crawl_sources_parallel,
    filter_markdown_for_extraction,
    pick_best_crawl_result,
    rank_crawl_results,
    resolve_source_urls,
    source_keywords,
)
from trade_integrations.dataflows.index_research.external_predictions.crawl_resilience import (
    should_run_searxng_fallback,
)
from trade_integrations.dataflows.index_research.external_predictions.browse_agent import (
    browse_enabled_for_source,
    browse_result_to_crawl_row,
    run_exploratory_browse,
)
from trade_integrations.dataflows.index_research.external_predictions.financial_expert_agent import (
    extract_forecast,
)
from trade_integrations.dataflows.index_research.external_predictions.screenshot_utils import (
    ScreenshotArtifacts,
    persist_screenshot_b64,
)
from trade_integrations.dataflows.index_research.external_predictions.search_agent import (
    passes_verified_quality_gates,
    progressive_search_until_forecast,
)
from trade_integrations.dataflows.index_research.external_predictions.navigation_paths import (
    persist_successful_exploratory_path,
    try_fast_path_then_exploratory,
)
from trade_integrations.dataflows.index_research.external_predictions.path_store import (
    touch_path_success,
)
from trade_integrations.dataflows.index_research.external_predictions.models import (
    ExternalPredictionRecord,
    ExternalPredictionSnapshot,
    ExternalPredictionSource,
    NavigationStep,
    utc_now_iso,
)
from trade_integrations.dataflows.index_research.external_predictions.source_registry import (
    clear_invalid_listing_saved_paths,
    get_source,
    watchlisted_sources,
)
from trade_integrations.dataflows.index_research.external_predictions.url_policy import (
    classify_page_kind,
)
from trade_integrations.dataflows.index_research.external_predictions.store import (
    load_source_prediction,
    persist_refresh_result,
    rebuild_snapshot,
)
from trade_integrations.dataflows.index_research.pipeline_log import PipelineLogger
from trade_integrations.dataflows.index_research.spot_fetch import fetch_index_spot

logger = logging.getLogger(__name__)


def _ensure_env_loaded() -> None:
    try:
        from trade_integrations.env import load_trade_env

        load_trade_env()
    except Exception:
        logger.debug("load_trade_env skipped", exc_info=True)


def _fetch_spot(symbol: str, pipeline: PipelineLogger | None = None) -> float | None:
    if pipeline:
        pipeline.info("spot", f"Fetching live {symbol} spot via OpenAlgo")
    try:
        result = fetch_index_spot(symbol)
        if result.spot > 0:
            if pipeline:
                pipeline.info("spot", f"Spot = {result.spot:,.0f}", source=result.source)
            return result.spot
        if pipeline:
            pipeline.warn("spot", result.error or "Spot unavailable")
    except Exception as exc:
        if pipeline:
            pipeline.warn("spot", f"Spot fetch failed: {exc}")
        logger.debug("spot fetch failed: %s", exc)
    return None


def _internal_forecast(
    symbol: str,
    horizon_days: int,
    pipeline: PipelineLogger | None = None,
) -> dict[str, Any] | None:
    if pipeline:
        pipeline.info("internal", "Loading internal model forecast for comparison overlay")
    try:
        from trade_integrations.context.hub import load_index_research_json

        doc = load_index_research_json(symbol)
        if doc is None:
            if pipeline:
                pipeline.warn("internal", "No index research artifact — comparison overlay skipped")
            return None
        pred = getattr(doc, "prediction", None) or {}
        if not isinstance(pred, dict):
            pred = {}
        as_of_raw = getattr(doc, "as_of", None)
        if hasattr(as_of_raw, "isoformat"):
            as_of = as_of_raw.isoformat()
        elif as_of_raw is not None:
            as_of = str(as_of_raw)
        else:
            as_of = None
        forecast = {
            "expected_return_pct": pred.get("expected_return_pct") or pred.get("return_pct"),
            "direction": pred.get("direction") or pred.get("view"),
            "confidence": pred.get("confidence"),
            "horizon_days": horizon_days,
            "as_of": as_of,
            "spot": getattr(doc, "spot", None),
        }
        if pipeline:
            pipeline.info(
                "internal",
                "Internal forecast loaded",
                direction=forecast.get("direction"),
                expected_return_pct=forecast.get("expected_return_pct"),
            )
        return forecast
    except Exception as exc:
        if pipeline:
            pipeline.warn("internal", f"Internal forecast load failed: {exc}")
        logger.debug("internal forecast load failed: %s", exc)
        return None


def _extract_from_crawl(
    src: ExternalPredictionSource,
    *,
    symbol: str,
    horizon_days: int,
    spot_val: float | None,
    url: str,
    title: str,
    markdown: str,
    screenshot_b64: str | None = None,
    pipeline: PipelineLogger | None = None,
) -> ExternalPredictionRecord:
    keywords = source_keywords(src, horizon_days=horizon_days)
    filtered = filter_markdown_for_extraction(
        markdown,
        keywords,
        horizon_days=horizon_days,
    )
    snippet = "\n".join(filtered.splitlines()[:12]) if filtered.strip() else "\n".join(
        (markdown or "").splitlines()[:12]
    )
    body = (markdown or "").strip() or filtered
    artifacts: ScreenshotArtifacts | None = None
    if screenshot_b64:
        try:
            artifacts = persist_screenshot_b64(
                symbol=symbol.upper(),
                source_id=src.id,
                screenshot_b64=screenshot_b64,
            )
            if artifacts is None:
                if pipeline:
                    pipeline.warn(
                        "screenshot",
                        "Screenshot payload present but decode/persist failed",
                        source_id=src.id,
                    )
            elif pipeline:
                pipeline.info(
                    "screenshot",
                    "Saved page screenshot artifacts",
                    source_id=src.id,
                    run_id=artifacts.run_id,
                    tiles=len(artifacts.m3_paths),
                )
        except Exception as exc:
            if pipeline:
                pipeline.warn("screenshot", f"Screenshot persist failed: {exc}", source_id=src.id)
            logger.debug("screenshot persist failed for %s: %s", src.id, exc)

    if pipeline:
        pipeline.info("extract", "Running expert agent extraction", source_id=src.id, url=url)
    return extract_forecast(
        source=src,
        horizon_days=horizon_days,
        spot=spot_val,
        title=title or src.display_name,
        url=url,
        snippet=snippet,
        body=body,
        symbol=symbol,
        screenshot_artifacts=artifacts,
        pipeline=pipeline,
    )


def _record_from_crawl_group(
    src: ExternalPredictionSource,
    rows: list[tuple[str, Any]],
    *,
    symbol: str,
    horizon_days: int,
    spot_val: float | None,
    pipeline: PipelineLogger | None = None,
    source_index: int | None = None,
    source_total: int | None = None,
    navigation_mode: str = "exploratory",
    fetch_method: str = "crawl4ai",
    navigation_steps: list[NavigationStep] | None = None,
    batch_registry: BatchUrlRegistry | None = None,
    tried_urls: set[str] | None = None,
) -> ExternalPredictionRecord:
    sym = symbol.upper()
    prefix = ""
    if source_index is not None and source_total is not None:
        prefix = f"[{source_index}/{source_total}] "

    urls = resolve_source_urls(src, symbol=sym, horizon_days=horizon_days)
    if not urls:
        if pipeline:
            pipeline.warn(
                "source",
                f"{prefix}{src.display_name}: no landing URLs configured",
                source_id=src.id,
            )
        record = ExternalPredictionRecord(
            source_id=src.id,
            symbol=sym,
            horizon_days=horizon_days,
            as_of=utc_now_iso()[:10],
            spot_at_fetch=spot_val,
            fetch_status="not_found",
            error_message="No landing URLs configured",
        )
        _, attempt = persist_refresh_result(record, symbol=sym)
        return attempt

    ranked_crawls = rank_crawl_results(
        rows,
        source_keywords(src, horizon_days=horizon_days),
        horizon_days=horizon_days,
        pipeline=pipeline,
        batch_registry=batch_registry,
        source=src,
    )
    if not ranked_crawls:
        errors = [row.error_message for _, row in rows if row.error_message]
        message = errors[0] if errors else "Crawl failed for all URLs"
        if pipeline:
            pipeline.warn(
                "source",
                f"{prefix}{src.display_name}: {message}",
                source_id=src.id,
            )
        error_prov: dict[str, Any] = {
            "urls_tried": list(dict.fromkeys(url for url, _ in rows)) or urls,
        }
        record = ExternalPredictionRecord(
            source_id=src.id,
            symbol=sym,
            horizon_days=horizon_days,
            as_of=utc_now_iso()[:10],
            spot_at_fetch=spot_val,
            fetch_status="error" if errors else "not_found",
            error_message=message,
            provenance=error_prov,
        )
        _, attempt = persist_refresh_result(record, symbol=sym)
        return attempt

    tried = tried_urls if tried_urls is not None else set()
    last_record: ExternalPredictionRecord | None = None
    for url, crawl in ranked_crawls:
        norm_url = str(url or "").strip().rstrip("/")
        if norm_url:
            tried.add(norm_url)
        screenshot_b64 = None
        if isinstance(crawl.metadata, dict):
            raw = crawl.metadata.get("screenshot_b64")
            if raw:
                screenshot_b64 = str(raw)
        record = _extract_from_crawl(
            src,
            symbol=sym,
            horizon_days=horizon_days,
            spot_val=spot_val,
            url=url,
            title=crawl.title,
            markdown=crawl.markdown,
            screenshot_b64=screenshot_b64,
            pipeline=pipeline,
        )
        record.as_of = utc_now_iso()[:10]
        record.spot_at_fetch = spot_val
        provenance: dict[str, Any] = {
            **dict(record.provenance or {}),
            "url": url,
            "title": crawl.title or record.provenance.get("title", ""),
            "fetch_method": fetch_method,
            "navigation_mode": navigation_mode,
            "elapsed_ms": crawl.elapsed_ms,
            "page_kind": classify_page_kind(url),
        }
        if isinstance(crawl.metadata, dict):
            vision_steps = crawl.metadata.get("vision_nav_steps")
            if vision_steps:
                provenance["vision_nav_trace"] = vision_steps
            if crawl.metadata.get("vision_nav"):
                if pipeline:
                    pipeline.info(
                        "vision_nav",
                        f"{prefix}{src.display_name}: crawl used vision recovery "
                        f"({crawl.metadata.get('vision_nav_rounds', 0)} round(s))",
                        source_id=src.id,
                        url=url[:120],
                    )
        record.provenance = provenance
        last_record = record
        if record.fetch_status == "stale":
            if pipeline:
                pipeline.info(
                    "source",
                    (
                        f"{prefix}{src.display_name}: article published "
                        f"{record.published_at or 'unknown'} outside 3d window — trying next candidate"
                    ),
                    source_id=src.id,
                    url=url[:120],
                )
            continue
        if record.fetch_status == "ok":
            if not passes_verified_quality_gates(
                record,
                src,
                url,
                title=crawl.title or "",
                content=crawl.markdown or "",
            ):
                if pipeline:
                    pipeline.info(
                        "source",
                        f"{prefix}{src.display_name}: crawl extract failed quality gates — next candidate",
                        source_id=src.id,
                        url=url[:120],
                    )
                last_record = record
                continue
            persist_successful_exploratory_path(
                src.id,
                horizon_days=horizon_days,
                url=url,
                steps=navigation_steps,
                pipeline=pipeline,
            )
            if batch_registry is not None:
                batch_registry.claim(url, src.id)
            mid = record.target.mid
            if pipeline:
                pipeline.info(
                    "source",
                    f"{prefix}{src.display_name}: extracted target {mid:,.0f}" if mid else f"{prefix}{src.display_name}: ok",
                    source_id=src.id,
                    direction=record.direction,
                    confidence=record.confidence,
                    model=record.extraction.get("model"),
                    url=url,
                )
            _, attempt = persist_refresh_result(record, symbol=sym)
            return attempt
        if pipeline:
            pipeline.warn(
                "source",
                f"{prefix}{src.display_name}: no usable forecast — {record.error_message or 'not found'}",
                source_id=src.id,
                url=url,
            )

    if last_record is not None:
        _, attempt = persist_refresh_result(last_record, symbol=sym)
        return attempt

    record = ExternalPredictionRecord(
        source_id=src.id,
        symbol=sym,
        horizon_days=horizon_days,
        as_of=utc_now_iso()[:10],
        spot_at_fetch=spot_val,
        fetch_status="not_found",
        error_message="No usable forecast from crawl candidates",
    )
    _, attempt = persist_refresh_result(record, symbol=sym)
    return attempt


def refresh_source(
    source_id: str,
    *,
    symbol: str = "NIFTY",
    horizon_days: int = 14,
    spot: float | None = None,
    pipeline: PipelineLogger | None = None,
    source_index: int | None = None,
    source_total: int | None = None,
    crawl_group: dict[str, list[tuple[str, Any]]] | None = None,
    batch_registry: BatchUrlRegistry | None = None,
    tried_urls: set[str] | None = None,
) -> ExternalPredictionRecord:
    src = get_source(source_id)
    if src is None:
        if pipeline:
            pipeline.error("source", f"Unknown source {source_id}")
        return ExternalPredictionRecord(
            source_id=source_id,
            symbol=symbol.upper(),
            horizon_days=horizon_days,
            fetch_status="error",
            error_message=f"Unknown source {source_id}",
        )

    from trade_integrations.dataflows.index_research.external_predictions.domain_utils import (
        native_domains,
    )

    sym = symbol.upper()
    if not native_domains(src) and not (src.domains or []):
        msg = f"No domains configured for {src.display_name}"
        if pipeline:
            pipeline.warn("source", msg, source_id=source_id)
        record = ExternalPredictionRecord(
            source_id=src.id,
            symbol=sym,
            horizon_days=horizon_days,
            fetch_status="error",
            error_message=msg,
        )
        _, attempt = persist_refresh_result(record, symbol=sym)
        return attempt

    if pipeline:
        prefix = ""
        if source_index is not None and source_total is not None:
            prefix = f"[{source_index}/{source_total}] "
        pipeline.info(
            "source",
            f"{prefix}Refreshing {src.display_name}",
            source_id=source_id,
            kind=src.kind,
        )

    spot_val = spot if spot is not None else _fetch_spot(sym, pipeline)
    tried = tried_urls if tried_urls is not None else set()

    if crawl_group is not None:
        rows = list(crawl_group.get(source_id, []))
    else:
        grouped = crawl_sources_parallel(
            [src],
            symbol=sym,
            horizon_days=horizon_days,
            pipeline=pipeline,
            attribution_owners=batch_registry.attribution_owners if batch_registry else None,
        )
        rows = grouped.get(source_id, [])

    for url, _row in rows:
        norm = str(url or "").strip().rstrip("/")
        if norm:
            tried.add(norm)

    replay_result, rows, exploratory_backup = try_fast_path_then_exploratory(
        src,
        horizon_days=horizon_days,
        exploratory_rows=rows,
        pipeline=pipeline,
    )
    used_fast = replay_result is not None and replay_result.success
    navigation_mode = "fast" if used_fast else "exploratory"
    fetch_method = "path_replay" if used_fast else "crawl4ai"
    navigation_steps: list[NavigationStep] | None = None

    try:
        record = _record_from_crawl_group(
            src,
            rows,
            symbol=sym,
            horizon_days=horizon_days,
            spot_val=spot_val,
            pipeline=pipeline,
            source_index=source_index,
            source_total=source_total,
            navigation_mode=navigation_mode,
            fetch_method=fetch_method,
            navigation_steps=navigation_steps,
            batch_registry=batch_registry,
            tried_urls=tried,
        )
        if used_fast and record.fetch_status != "ok":
            if exploratory_backup:
                if pipeline:
                    pipeline.warn(
                        "navigation",
                        "Fast-path extract failed — falling back to exploratory crawl batch",
                        source_id=source_id,
                        error=record.error_message or "not_found",
                    )
                navigation_mode = "exploratory"
                fetch_method = "crawl4ai"
                record = _record_from_crawl_group(
                    src,
                    exploratory_backup,
                    symbol=sym,
                    horizon_days=horizon_days,
                    spot_val=spot_val,
                    pipeline=pipeline,
                    source_index=source_index,
                    source_total=source_total,
                    navigation_mode=navigation_mode,
                    fetch_method=fetch_method,
                    navigation_steps=None,
                    batch_registry=batch_registry,
                    tried_urls=tried,
                )
        elif used_fast and record.fetch_status == "ok":
            touch_path_success(src.id, horizon_days=horizon_days)

        if record.fetch_status != "ok":
            trigger = "crawl_no_forecast"
            if rows:
                errors = [row.error_message for _, row in rows if getattr(row, "error_message", None)]
                message = errors[0] if errors else "Crawl failed for all URLs"
                should_search, trigger = should_run_searxng_fallback(rows, message)
                if not should_search:
                    trigger = "crawl_no_forecast"
            if pipeline:
                pipeline.info(
                    "search_agent",
                    f"{prefix}{src.display_name}: progressive search ({trigger})",
                    source_id=source_id,
                )
            outcome = progressive_search_until_forecast(
                src,
                symbol=sym,
                horizon_days=horizon_days,
                spot=spot_val,
                tried_urls=tried,
                batch_registry=batch_registry,
                pipeline=pipeline,
                searxng_trigger=trigger,
            )
            if outcome.record is not None and outcome.record.fetch_status == "ok":
                _, record = persist_refresh_result(outcome.record, symbol=sym)
            elif outcome.record is not None:
                record = outcome.record

        if record.fetch_status != "ok":
            crawl_best = pick_best_crawl_result(
                rows,
                source_keywords(src, horizon_days=horizon_days),
                horizon_days=horizon_days,
                pipeline=None,
                batch_registry=batch_registry,
                source=src,
            )
            browse_from_landing = False
            if not browse_enabled_for_source(src):
                browse_from_landing = crawl_best is None and bool(src.landing_urls)
            should_browse = crawl_best is None and (
                browse_enabled_for_source(src) or browse_from_landing
            )
            if should_browse:
                browse = run_exploratory_browse(
                    src,
                    horizon_days=horizon_days,
                    pipeline=pipeline,
                    include_landing_fallback=browse_from_landing,
                )
                if browse.success and browse.url:
                    browse_rows = [browse_result_to_crawl_row(browse)]
                    navigation_steps = list(browse.trace.steps)
                    record = _record_from_crawl_group(
                        src,
                        browse_rows,
                        symbol=sym,
                        horizon_days=horizon_days,
                        spot_val=spot_val,
                        pipeline=pipeline,
                        source_index=source_index,
                        source_total=source_total,
                        navigation_mode="browse_agent",
                        fetch_method="browse_agent",
                        navigation_steps=navigation_steps,
                        batch_registry=batch_registry,
                        tried_urls=tried,
                    )
                elif pipeline and browse.error_message:
                    pipeline.warn(
                        "browse",
                        f"Exploratory browse last resort failed ({browse.error_message})",
                        source_id=source_id,
                    )

        prov = dict(record.provenance or {})
        if prov.get("navigation_mode") not in {"searxng_fallback", "progressive_search"}:
            record.provenance = {
                **prov,
                "navigation_mode": navigation_mode,
                "fetch_method": fetch_method,
            }
        return record
    except Exception as exc:
        logger.warning("refresh failed for %s: %s", source_id, exc)
        if pipeline:
            pipeline.error("source", f"{src.display_name}: {exc}", source_id=source_id)
        record = ExternalPredictionRecord(
            source_id=source_id,
            symbol=sym,
            horizon_days=horizon_days,
            as_of=utc_now_iso()[:10],
            spot_at_fetch=spot_val,
            fetch_status="error",
            error_message=str(exc),
        )
        _, attempt = persist_refresh_result(record, symbol=sym)
        return attempt


def refresh_all_external_predictions(
    *,
    symbol: str = "NIFTY",
    horizon_days: int = 14,
    min_interval_sec: float = 0.0,
    pipeline: PipelineLogger | None = None,
    on_source_complete: Callable[[str, ExternalPredictionRecord, ExternalPredictionSnapshot], None]
    | None = None,
) -> ExternalPredictionSnapshot:
    """Run refresh batch. Caller should hold ``external_refresh_lock`` for single-flight."""
    _ensure_env_loaded()
    sym = symbol.upper()
    if pipeline:
        pipeline.info(
            "refresh",
            f"Starting external predictions refresh for {sym} ({horizon_days}d horizon)",
        )
    spot = _fetch_spot(sym, pipeline)
    try:
        from trade_integrations.dataflows.index_research.external_predictions.financial_expert_context import (
            build_and_save_expert_context,
        )

        expert_ctx = build_and_save_expert_context(
            symbol=sym,
            horizon_days=horizon_days,
            spot=spot,
        )
        if pipeline:
            pipeline.info(
                "expert_context",
                "Financial expert context built",
                as_of=expert_ctx.get("as_of"),
                movers=len(expert_ctx.get("top_factor_movers") or []),
            )
    except Exception as exc:
        logger.warning("expert context build failed: %s", exc)
        if pipeline:
            pipeline.warn("expert_context", f"Context build skipped: {exc}")
    internal = _internal_forecast(sym, horizon_days, pipeline)
    fetched_at = utc_now_iso()
    sources = watchlisted_sources()
    try:
        cleared = clear_invalid_listing_saved_paths(persist=True)
        if pipeline and cleared:
            pipeline.info(
                "navigation",
                f"Cleared {cleared} stale listing/topic saved path(s)",
            )
    except Exception as exc:
        logger.debug("clear_invalid_listing_saved_paths skipped: %s", exc)

    batch_registry = BatchUrlRegistry()
    if pipeline:
        pipeline.info("refresh", f"Watchlisted sources: {len(sources)}")
        stats = crawl4ai_queue_stats()
        pipeline.info(
            "crawl4ai",
            "Queue ready",
            installed=stats.get("installed"),
            max_parallel=stats.get("max_parallel"),
            waiting=stats.get("waiting"),
        )

    tried_urls: set[str] = set()

    refresh_attempt_failures = 0
    for idx, src in enumerate(sources, start=1):
        try:
            attempt = refresh_source(
                src.id,
                symbol=sym,
                horizon_days=horizon_days,
                spot=spot,
                pipeline=pipeline,
                source_index=idx,
                source_total=len(sources),
                batch_registry=batch_registry,
                tried_urls=tried_urls,
            )
        except Exception as exc:
            logger.exception("refresh failed for %s: %s", src.id, exc)
            if pipeline:
                pipeline.error("source", f"{src.display_name}: {exc}", source_id=src.id)
            err_record = ExternalPredictionRecord(
                source_id=src.id,
                symbol=sym,
                horizon_days=horizon_days,
                as_of=utc_now_iso()[:10],
                spot_at_fetch=spot,
                fetch_status="error",
                error_message=str(exc),
            )
            _, attempt = persist_refresh_result(err_record, symbol=sym)

        stored = load_source_prediction(src.id, symbol=sym, horizon_days=horizon_days)
        if (
            stored is not None
            and stored.fetch_status == "ok"
            and attempt.fetch_status != "ok"
        ):
            refresh_attempt_failures += 1

        if on_source_complete is not None:
            partial = rebuild_snapshot(
                symbol=sym,
                horizon_days=horizon_days,
                internal_forecast=internal,
                fetched_at=fetched_at,
                refresh_attempt_failures=refresh_attempt_failures,
            )
            try:
                on_source_complete(src.id, attempt, partial)
            except Exception as exc:
                logger.warning("on_source_complete failed for %s: %s", src.id, exc)

    snapshot = rebuild_snapshot(
        symbol=sym,
        horizon_days=horizon_days,
        internal_forecast=internal,
        fetched_at=fetched_at,
        refresh_completed_at=utc_now_iso(),
        refresh_attempt_failures=refresh_attempt_failures,
    )
    ok_count = sum(1 for p in snapshot.predictions if p.fetch_status == "ok")
    if pipeline:
        batch = crawl4ai_queue_stats().get("last_batch") or {}
        pipeline.info(
            "refresh",
            (
                f"Refresh complete — {ok_count}/{len(snapshot.predictions)} sources with forecasts"
                f" ({snapshot.sources_error} errors, {snapshot.sources_not_found} not found"
                f"{f', {snapshot.refresh_attempt_failures} cached after failed refresh' if snapshot.refresh_attempt_failures else ''})"
            ),
            fetched_at=snapshot.fetched_at,
            crawl_elapsed_ms=batch.get("elapsed_ms"),
            sources_ok=snapshot.sources_ok,
            sources_error=snapshot.sources_error,
            sources_not_found=snapshot.sources_not_found,
            had_errors=snapshot.had_errors,
            refresh_attempt_failures=snapshot.refresh_attempt_failures,
        )
    return snapshot
