"""Bounded parallel Crawl4AI fetch — sole entry point for browser crawls."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from trade_integrations.context.hub import get_hub_dir

logger = logging.getLogger(__name__)

_DATA_DIR = Path("_data") / "crawl4ai"
_LAST_BATCH = "last_batch.json"
_WAITING = "waiting.json"
_IN_FLIGHT = "in_flight.json"
_DEFAULT_MAX_PARALLEL = 4


@dataclass
class CrawlPageResult:
    url: str
    success: bool
    markdown: str = ""
    title: str = ""
    error_message: str = ""
    elapsed_ms: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)


def crawl4ai_is_installed() -> bool:
    try:
        import crawl4ai  # noqa: F401

        return True
    except ImportError:
        return False


def _crawl4ai_data_dir() -> Path:
    path = get_hub_dir() / _DATA_DIR
    path.mkdir(parents=True, exist_ok=True)
    return path


def _read_json_int(path: Path, key: str) -> int:
    if not path.is_file():
        return 0
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return max(0, int(data.get(key) or 0))
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return 0


def _write_json_int(path: Path, key: str, value: int) -> None:
    path.write_text(json.dumps({key: max(0, value)}), encoding="utf-8")


def _waiting_path() -> Path:
    return _crawl4ai_data_dir() / _WAITING


def _in_flight_path() -> Path:
    return _crawl4ai_data_dir() / _IN_FLIGHT


def _last_batch_path() -> Path:
    return _crawl4ai_data_dir() / _LAST_BATCH


def _max_parallel() -> int:
    raw = os.environ.get("CRAWL4AI_MAX_PARALLEL", str(_DEFAULT_MAX_PARALLEL)).strip()
    try:
        return max(1, int(raw))
    except ValueError:
        return _DEFAULT_MAX_PARALLEL


def _adjust_counter(path: Path, key: str, delta: int) -> None:
    import fcntl

    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = path.with_suffix(".lock")
    fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        current = _read_json_int(path, key)
        _write_json_int(path, key, current + delta)
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def _write_last_batch(payload: dict[str, Any]) -> None:
    _last_batch_path().write_text(json.dumps(payload, indent=2), encoding="utf-8")


def crawl4ai_queue_stats() -> dict[str, Any]:
    last_batch: dict[str, Any] = {}
    path = _last_batch_path()
    if path.is_file():
        try:
            last_batch = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            last_batch = {}
    return {
        "installed": crawl4ai_is_installed(),
        "max_parallel": _max_parallel(),
        "waiting": _read_json_int(_waiting_path(), "waiting"),
        "in_flight": _read_json_int(_in_flight_path(), "in_flight"),
        "last_batch": last_batch,
    }


def _browser_config() -> Any:
    from crawl4ai import BrowserConfig

    return BrowserConfig(
        headless=True,
        enable_stealth=True,
        light_mode=True,
        text_mode=False,
    )


def _run_config(*, score_links: bool = False) -> Any:
    from crawl4ai import CacheMode, CrawlerRunConfig

    kwargs: dict[str, Any] = {
        "cache_mode": CacheMode.BYPASS,
        "word_count_threshold": 5,
    }
    if score_links:
        try:
            from crawl4ai import LinkPreviewConfig

            kwargs["score_links"] = True
            kwargs["link_preview_config"] = LinkPreviewConfig(
                query="Nifty 50 index target forecast outlook analyst",
                score_threshold=0.25,
            )
        except ImportError:
            logger.debug("LinkPreviewConfig unavailable; listing crawl without link scoring")
    return CrawlerRunConfig(**kwargs)


def _serialize_native_links(links_obj: Any) -> list[dict[str, Any]]:
    """Normalize Crawl4AI internal links for downstream discovery."""
    if links_obj is None:
        return []
    internal = getattr(links_obj, "internal", None)
    if internal is None and isinstance(links_obj, dict):
        internal = links_obj.get("internal")
    rows: list[dict[str, Any]] = []
    for link in internal or []:
        if isinstance(link, dict):
            href = str(link.get("href") or "").strip()
            text = str(link.get("text") or link.get("title") or "").strip()
            total_score = link.get("total_score")
        else:
            href = str(getattr(link, "href", "") or "").strip()
            text = str(getattr(link, "text", "") or getattr(link, "title", "") or "").strip()
            total_score = getattr(link, "total_score", None)
        if not href:
            continue
        rows.append(
            {
                "href": href,
                "text": text,
                "title": text,
                "total_score": total_score,
            }
        )
    return rows


async def crawl_urls_parallel(
    urls: list[str],
    *,
    max_parallel: int | None = None,
    pipeline: Any | None = None,
    score_links: bool = False,
) -> list[CrawlPageResult]:
    """Fetch URLs concurrently via one shared AsyncWebCrawler process."""
    from trade_integrations.dataflows import source_availability

    cleaned = [u.strip() for u in urls if str(u or "").strip()]
    if not cleaned:
        return []

    if not crawl4ai_is_installed():
        msg = "crawl4ai not installed — run: pip install 'trade-stack[external-predictions]' && crawl4ai-setup"
        if pipeline:
            pipeline.error("crawl4ai", msg)
        return [CrawlPageResult(url=u, success=False, error_message=msg) for u in cleaned]

    if not source_availability.should_attempt("crawl4ai", "fetch"):
        msg = "Crawl4AI circuit open — browser fetch temporarily unavailable"
        if pipeline:
            pipeline.warn("crawl4ai", msg)
        return [CrawlPageResult(url=u, success=False, error_message=msg) for u in cleaned]

    parallel = max_parallel or _max_parallel()
    if pipeline:
        pipeline.info(
            "crawl4ai",
            f"Launching stealth parallel crawl ({len(cleaned)} URL(s), max_parallel={parallel})",
        )

    _adjust_counter(_waiting_path(), "waiting", len(cleaned))
    batch_started = time.time()
    results: list[CrawlPageResult] = []
    batch_error = ""

    try:
        from crawl4ai import AsyncWebCrawler

        semaphore = asyncio.Semaphore(parallel)

        async with AsyncWebCrawler(config=_browser_config()) as crawler:

            async def _crawl_one(url: str) -> CrawlPageResult:
                started = time.time()
                _adjust_counter(_in_flight_path(), "in_flight", 1)
                _adjust_counter(_waiting_path(), "waiting", -1)
                if pipeline:
                    pipeline.info("crawl4ai", f"Fetching {url[:100]}", url=url)
                try:
                    async with semaphore:
                        result = await crawler.arun(url=url, config=_run_config(score_links=score_links))
                    elapsed_ms = (time.time() - started) * 1000.0
                    if result.success:
                        markdown = str(getattr(result, "markdown", "") or "")
                        title = ""
                        metadata = dict(getattr(result, "metadata", None) or {})
                        native_links = _serialize_native_links(getattr(result, "links", None))
                        if native_links:
                            metadata["links"] = native_links
                        if metadata:
                            title = str(metadata.get("title") or "")
                        if pipeline:
                            pipeline.info(
                                "crawl4ai",
                                f"OK ({len(markdown)} chars, {elapsed_ms:.0f}ms)",
                                url=url,
                            )
                        return CrawlPageResult(
                            url=url,
                            success=True,
                            markdown=markdown,
                            title=title,
                            elapsed_ms=elapsed_ms,
                            metadata=metadata,
                        )
                    error_message = str(getattr(result, "error_message", "") or "Crawl failed")
                    if pipeline:
                        pipeline.warn("crawl4ai", error_message, url=url)
                    return CrawlPageResult(
                        url=url,
                        success=False,
                        error_message=error_message,
                        elapsed_ms=elapsed_ms,
                    )
                except Exception as exc:
                    elapsed_ms = (time.time() - started) * 1000.0
                    if pipeline:
                        pipeline.warn("crawl4ai", str(exc), url=url)
                    return CrawlPageResult(
                        url=url,
                        success=False,
                        error_message=str(exc),
                        elapsed_ms=elapsed_ms,
                    )
                finally:
                    _adjust_counter(_in_flight_path(), "in_flight", -1)

            gathered = await asyncio.gather(*[_crawl_one(url) for url in cleaned], return_exceptions=True)
            for url, item in zip(cleaned, gathered):
                if isinstance(item, Exception):
                    results.append(CrawlPageResult(url=url, success=False, error_message=str(item)))
                else:
                    results.append(item)

        ok_count = sum(1 for row in results if row.success)
        if ok_count:
            source_availability.record_success("crawl4ai", "fetch")
        else:
            source_availability.record_failure("crawl4ai", "fetch", "all URLs failed")
    except Exception as exc:
        batch_error = str(exc)
        source_availability.record_failure("crawl4ai", "fetch", exc)
        if pipeline:
            pipeline.error("crawl4ai", batch_error)
        results = [
            CrawlPageResult(url=u, success=False, error_message=batch_error or str(exc))
            for u in cleaned
        ]
    finally:
        remaining = _read_json_int(_waiting_path(), "waiting")
        if remaining > 0:
            _adjust_counter(_waiting_path(), "waiting", -remaining)
        elapsed_ms = (time.time() - batch_started) * 1000.0
        _write_last_batch(
            {
                "url_count": len(cleaned),
                "success_count": sum(1 for row in results if row.success),
                "elapsed_ms": round(elapsed_ms, 1),
                "max_parallel": parallel,
                "error": batch_error,
                "finished_at": time.time(),
            }
        )
        if pipeline:
            pipeline.info(
                "crawl4ai",
                f"Batch complete — {sum(1 for row in results if row.success)}/{len(cleaned)} OK "
                f"in {elapsed_ms:.0f}ms",
            )

    return results


def crawl_urls_parallel_sync(
    urls: list[str],
    *,
    max_parallel: int | None = None,
    pipeline: Any | None = None,
    score_links: bool = False,
) -> list[CrawlPageResult]:
    """Sync wrapper for refresh workers running outside an event loop."""
    return asyncio.run(
        crawl_urls_parallel(
            urls,
            max_parallel=max_parallel,
            pipeline=pipeline,
            score_links=score_links,
        )
    )


def reset_crawl4ai_client_for_tests() -> None:
    """Clear hub-side queue metadata (tests only)."""
    for path in (_waiting_path(), _in_flight_path(), _last_batch_path()):
        try:
            if path.is_file():
                path.unlink()
        except OSError:
            logger.debug("could not remove %s during test reset", path, exc_info=True)
