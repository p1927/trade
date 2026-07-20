"""Batch refresh of watchlisted external prediction sources."""

from __future__ import annotations

import logging
from typing import Any

from trade_integrations.dataflows.crawl4ai_client import crawl4ai_queue_stats
from trade_integrations.dataflows.index_research.external_predictions.crawl4ai_fetcher import (
    crawl_sources_parallel,
    filter_markdown_for_extraction,
    pick_best_crawl_result,
    resolve_source_urls,
    source_keywords,
)
from trade_integrations.dataflows.index_research.external_predictions.extractor import (
    extract_prediction_from_text,
)
from trade_integrations.dataflows.index_research.external_predictions.models import (
    ExternalPredictionRecord,
    ExternalPredictionSnapshot,
    ExternalPredictionSource,
    utc_now_iso,
)
from trade_integrations.dataflows.index_research.external_predictions.source_registry import (
    get_source,
    watchlisted_sources,
)
from trade_integrations.dataflows.index_research.external_predictions.store import (
    rebuild_snapshot,
    upsert_prediction,
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
    pipeline: PipelineLogger | None = None,
) -> ExternalPredictionRecord:
    keywords = source_keywords(src, horizon_days=horizon_days)
    body = filter_markdown_for_extraction(
        markdown,
        keywords,
        horizon_days=horizon_days,
    )
    snippet = "\n".join(body.splitlines()[:12])
    if pipeline:
        pipeline.info("extract", "Running LLM / regex extraction", source_id=src.id, url=url)
    return extract_prediction_from_text(
        source=src,
        horizon_days=horizon_days,
        spot=spot_val,
        title=title or src.display_name,
        url=url,
        snippet=snippet,
        body=body,
        symbol=symbol,
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
        upsert_prediction(record, symbol=sym)
        return record

    best = pick_best_crawl_result(
        rows,
        source_keywords(src, horizon_days=horizon_days),
        horizon_days=horizon_days,
        pipeline=pipeline,
    )
    if best is None:
        errors = [row.error_message for _, row in rows if row.error_message]
        message = errors[0] if errors else "Crawl failed for all URLs"
        if pipeline:
            pipeline.warn(
                "source",
                f"{prefix}{src.display_name}: {message}",
                source_id=src.id,
            )
        record = ExternalPredictionRecord(
            source_id=src.id,
            symbol=sym,
            horizon_days=horizon_days,
            as_of=utc_now_iso()[:10],
            spot_at_fetch=spot_val,
            fetch_status="error" if errors else "not_found",
            error_message=message,
            provenance={"urls_tried": urls},
        )
        upsert_prediction(record, symbol=sym)
        return record

    url, crawl = best
    record = _extract_from_crawl(
        src,
        symbol=sym,
        horizon_days=horizon_days,
        spot_val=spot_val,
        url=url,
        title=crawl.title,
        markdown=crawl.markdown,
        pipeline=pipeline,
    )
    record.as_of = utc_now_iso()[:10]
    record.spot_at_fetch = spot_val
    record.provenance = {
        **dict(record.provenance or {}),
        "url": url,
        "title": crawl.title or record.provenance.get("title", ""),
        "fetch_method": "crawl4ai",
        "elapsed_ms": crawl.elapsed_ms,
    }
    if record.fetch_status == "ok":
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
    else:
        if pipeline:
            pipeline.warn(
                "source",
                f"{prefix}{src.display_name}: no usable forecast — {record.error_message or 'not found'}",
                source_id=src.id,
                url=url,
            )
    upsert_prediction(record, symbol=sym)
    return record


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

    sym = symbol.upper()
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

    if crawl_group is not None:
        rows = crawl_group.get(source_id, [])
    else:
        grouped = crawl_sources_parallel(
            [src],
            symbol=sym,
            horizon_days=horizon_days,
            pipeline=pipeline,
        )
        rows = grouped.get(source_id, [])

    try:
        return _record_from_crawl_group(
            src,
            rows,
            symbol=sym,
            horizon_days=horizon_days,
            spot_val=spot_val,
            pipeline=pipeline,
            source_index=source_index,
            source_total=source_total,
        )
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
        upsert_prediction(record, symbol=sym)
        return record


def refresh_all_external_predictions(
    *,
    symbol: str = "NIFTY",
    horizon_days: int = 14,
    min_interval_sec: float = 0.0,
    pipeline: PipelineLogger | None = None,
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
    internal = _internal_forecast(sym, horizon_days, pipeline)
    sources = watchlisted_sources()
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

    crawl_group = crawl_sources_parallel(
        sources,
        symbol=sym,
        horizon_days=horizon_days,
        pipeline=pipeline,
    )

    for idx, src in enumerate(sources, start=1):
        try:
            refresh_source(
                src.id,
                symbol=sym,
                horizon_days=horizon_days,
                spot=spot,
                pipeline=pipeline,
                source_index=idx,
                source_total=len(sources),
                crawl_group=crawl_group,
            )
        except Exception as exc:
            logger.exception("refresh failed for %s: %s", src.id, exc)
            if pipeline:
                pipeline.error("source", f"{src.display_name}: {exc}", source_id=src.id)
            upsert_prediction(
                ExternalPredictionRecord(
                    source_id=src.id,
                    symbol=sym,
                    horizon_days=horizon_days,
                    as_of=utc_now_iso()[:10],
                    spot_at_fetch=spot,
                    fetch_status="error",
                    error_message=str(exc),
                ),
                symbol=sym,
            )

    snapshot = rebuild_snapshot(
        symbol=sym,
        horizon_days=horizon_days,
        internal_forecast=internal,
        fetched_at=utc_now_iso(),
    )
    ok_count = sum(1 for p in snapshot.predictions if p.fetch_status == "ok")
    if pipeline:
        batch = crawl4ai_queue_stats().get("last_batch") or {}
        pipeline.info(
            "refresh",
            f"Refresh complete — {ok_count}/{len(snapshot.predictions)} sources with forecasts",
            fetched_at=snapshot.fetched_at,
            crawl_elapsed_ms=batch.get("elapsed_ms"),
        )
    return snapshot
