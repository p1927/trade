"""Unified hub news ingest — all live sources through ``news_hub_bridge``."""

from __future__ import annotations

import logging
import os
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from typing import Any

logger = logging.getLogger(__name__)

_ALL_SOURCES = frozenset({"rss", "searxng", "searxng_global", "moneycontrol", "watcher"})
_LIGHT_SOURCES = frozenset({"rss"})
_HEAVY_SOURCES = _ALL_SOURCES - _LIGHT_SOURCES
_INDEX_KEYWORDS = re.compile(
    r"\b(nifty|sensex|bank nifty|banknifty|rbi|fii|dii|repo|crude|oil|rupee|"
    r"market|bse|nse|inflation|gdp|fed|geopolit|tariff|budget)\b",
    re.I,
)


def _parse_sources(sources: str | list[str] | None) -> set[str]:
    if sources is None or sources == "all":
        return set(_ALL_SOURCES)
    if isinstance(sources, str):
        parts = {p.strip().lower() for p in sources.split(",") if p.strip()}
        return parts & _ALL_SOURCES
    return {str(s).strip().lower() for s in sources} & _ALL_SOURCES


def _light_ingest_allows_heavy_sources() -> bool:
    return os.getenv("HUB_NEWS_LIGHT_ALLOW_HEAVY", "").strip().lower() in {"1", "true", "yes"}


def _apply_light_source_guard(selected: set[str], *, ingest_mode: str) -> set[str]:
    """Light scheduled ingest stays on fast sources unless explicitly overridden."""
    if ingest_mode != "light" or _light_ingest_allows_heavy_sources():
        return selected
    filtered = selected & _LIGHT_SOURCES
    dropped = sorted(selected & _HEAVY_SOURCES)
    if dropped:
        logger.warning(
            "light ingest dropping heavy sources %s (set HUB_NEWS_LIGHT_ALLOW_HEAVY=1 to override)",
            dropped,
        )
    return filtered if filtered else set(_LIGHT_SOURCES)


def _merge_stats(out: dict[str, Any], source: str, stats: dict[str, Any]) -> None:
    out["sources"][source] = dict(stats)
    for key in ("queued", "ingested", "verified", "created", "updated", "error"):
        if key in stats:
            out["totals"][key] = int(out["totals"].get(key) or 0) + int(stats.get(key) or 0)


def _sync_distill_limit_for_mode(mode: str) -> int:
    """Light ingest queues refs only; entity drain job runs MiniMax distillation."""
    if mode == "light":
        try:
            return max(0, int(os.getenv("HUB_NEWS_LIGHT_SYNC_DISTILL_LIMIT", "0")))
        except ValueError:
            return 0
    try:
        return max(0, int(os.getenv("HUB_NEWS_SYNC_DISTILL_LIMIT", "10")))
    except ValueError:
        return 10


def _ingest_rss(
    *,
    ticker: str,
    limit_per_feed: int,
    sync_distill_limit: int | None = None,
) -> dict[str, Any]:
    from trade_integrations.dataflows.news_hub_bridge import ingest_rss_entries
    from trade_integrations.dataflows.rss_feeds import (
        _fetch_one_feed,
        _resolve_url,
        get_sentiment_rss_feeds,
    )

    feeds = get_sentiment_rss_feeds()
    totals = {"feeds": len(feeds), "queued": 0, "ingested": 0, "entries": 0, "errors": 0}
    if not feeds:
        return totals

    def _fetch_feed(index: int, feed: dict[str, Any]) -> tuple[str, str, list[dict[str, Any]] | None, str | None]:
        label = feed.get("label") or "rss"
        url = _resolve_url(feed["url"], ticker)
        try:
            _, entries = _fetch_one_feed(
                label,
                url,
                limit_per_feed,
                timeout=10.0,
                inter_request_delay=0.0,
                is_first=(index == 0),
            )
            return label, url, entries, None
        except Exception as exc:
            return label, url, None, str(exc)

    workers = min(6, max(1, len(feeds)))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {
            pool.submit(_fetch_feed, index, feed): index for index, feed in enumerate(feeds)
        }
        for future in as_completed(futures):
            label, url, entries, error = future.result()
            if error:
                logger.warning("RSS fetch failed for %s: %s", label, error)
                totals["errors"] += 1
                continue
            if not entries:
                continue
            totals["entries"] += len(entries)
            stats = ingest_rss_entries(
                entries,
                ticker=ticker,
                label=label,
                feed_url=url,
                sync_distill_limit=sync_distill_limit,
            )
            totals["queued"] += int(stats.get("queued") or stats.get("ingested") or 0)
            totals["ingested"] += int(stats.get("ingested") or 0)
    return totals


def _ingest_searxng_ticker(
    *,
    ticker: str,
    lookback_days: int,
    sync_distill_limit: int | None = None,
) -> dict[str, Any]:
    from trade_integrations.dataflows.news_hub_bridge import ingest_searxng_results
    from trade_integrations.dataflows.searxng_news import _format_results, _search

    end = datetime.now(timezone.utc).date()
    start = end - timedelta(days=lookback_days)
    results = _search(f"{ticker} stock news", limit=40)
    if not results:
        return {"results": 0, "queued": 0, "ingested": 0}
    stats = ingest_searxng_results(
        results,
        ticker=ticker,
        collection_day=end.isoformat(),
        sync_distill_limit=sync_distill_limit,
    )
    _format_results(
        results,
        header=f"{ticker} News",
        start_dt=datetime.combine(start, datetime.min.time()),
        end_dt=datetime.combine(end, datetime.min.time()),
        limit=20,
    )
    stats["results"] = len(results)
    return stats


def _ingest_searxng_global(
    *,
    lookback_days: int,
    sync_distill_limit: int | None = None,
) -> dict[str, Any]:
    from trade_integrations.dataflows.news_hub_bridge import ingest_searxng_results
    from trade_integrations.dataflows.searxng_news import _search
    from tradingagents.dataflows.config import get_config

    end = datetime.now(timezone.utc).date()
    config = get_config()
    limit = int(config.get("global_news_article_limit") or 15)
    all_results: list[dict] = []
    seen: set[str] = set()
    for query in config.get("global_news_queries") or []:
        enriched = f"{query} India markets moneycontrol economictimes livemint"
        for result in _search(str(enriched), limit):
            title = (result.get("title") or "").strip()
            if title and title not in seen:
                seen.add(title)
                all_results.append(result)
        if len(all_results) >= limit:
            break
    if not all_results:
        return {"results": 0, "queued": 0, "ingested": 0}
    stats = ingest_searxng_results(
        all_results,
        ticker="NIFTY",
        kind="global",
        collection_day=end.isoformat(),
        sync_distill_limit=sync_distill_limit,
    )
    stats["results"] = len(all_results)
    return stats


def _ingest_moneycontrol_macro(
    *,
    ticker: str,
    limit: int = 25,
    sync_distill_limit: int | None = None,
) -> dict[str, Any]:
    from trade_integrations.dataflows.company_research.sources.moneycontrol_rss import (
        MONEYCONTROL_RSS_URLS,
        _fetch_url,
    )
    from trade_integrations.dataflows.news_hub_bridge import ingest_rows_to_hub

    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for url in MONEYCONTROL_RSS_URLS:
        for entry in _fetch_url(url, limit=limit):
            title = str(entry.get("title") or "").strip()
            if not title:
                continue
            if ticker.upper() not in ("NIFTY", "SENSEX", "BANKNIFTY") and ticker.upper() not in title.upper():
                if not _INDEX_KEYWORDS.search(title):
                    continue
            key = title.lower()
            if key in seen:
                continue
            seen.add(key)
            day = str(entry.get("date") or "")[:10]
            rows.append(
                {
                    "title": title[:500],
                    "summary": str(entry.get("summary") or "")[:2000],
                    "url": str(entry.get("url") or "").strip()[:2000],
                    "source": str(entry.get("source") or "moneycontrol_rss"),
                    "published_at": f"{day}T09:00:00+00:00" if day else "",
                }
            )
    if not rows:
        return {"rows": 0, "queued": 0, "ingested": 0}
    stats = ingest_rows_to_hub(rows, ticker=ticker, sync_distill_limit=sync_distill_limit)
    stats["rows"] = len(rows)
    return stats


def _ingest_watcher(
    *,
    tickers: list[str],
    since_hours: int,
    sync_distill_limit: int | None = None,
) -> dict[str, Any]:
    from trade_integrations.monitor.news_watcher import scan_material_news

    since = datetime.now(timezone.utc) - timedelta(hours=since_hours)
    totals = {"tickers": {}, "material": 0, "queued": 0, "ingested": 0}
    for sym in tickers:
        material = scan_material_news(sym, since, exclude_seen=False)
        totals["material"] += len(material)
        totals["tickers"][sym] = len(material)
        if not material:
            continue
        from trade_integrations.dataflows.news_hub_bridge import ingest_rows_to_hub

        rows = [
            {
                "title": item.title,
                "summary": "",
                "url": item.url,
                "source": "material_news",
                "published_at": (
                    item.pub_date.isoformat()
                    if item.pub_date is not None
                    else datetime.now(timezone.utc).isoformat()
                ),
            }
            for item in material
        ]
        stats = ingest_rows_to_hub(rows, ticker=sym, sync_distill_limit=sync_distill_limit)
        totals["queued"] += int(stats.get("queued") or stats.get("ingested") or 0)
        totals["ingested"] += int(stats.get("ingested") or 0)
    return totals


def hub_ingest_snapshot(*, ticker: str = "NIFTY") -> dict[str, Any]:
    """Lightweight hub ingest metadata for company research docs."""
    from trade_integrations.dataflows.news_hub_bridge import staging_queue_stats

    sym = ticker.strip().upper()
    return {
        "ticker": sym,
        "scope": "micro" if sym not in {"NIFTY", "SENSEX", "BANKNIFTY", "NIFTYMID"} else "index",
        "staging": staging_queue_stats(ticker=sym),
    }


def run_hub_news_ingest(
    *,
    ticker: str = "NIFTY",
    sources: str | list[str] | None = "all",
    mode: str | None = None,
    lookback_days: int | None = None,
    rss_limit_per_feed: int = 10,
    watcher_since_hours: int = 6,
    watcher_tickers: list[str] | None = None,
) -> dict[str, Any]:
    """Fetch from configured sources and ingest into hub staging.

    ``mode`` may be ``full`` or ``light`` — loads sources/lookback from
    :func:`load_news_pipeline_config` when ``sources`` is not explicitly set.
    """
    from trade_integrations.dataflows.index_research.pipeline_cancel import check_pipeline_cancel
    from trade_integrations.hub_storage.news_pipeline_config import load_news_pipeline_config
    from trade_integrations.dataflows.index_research.news_market_context import (
        get_market_context_for_pipeline,
        refresh_index_market_context,
    )

    check_pipeline_cancel()
    from trade_integrations.dataflows.hub_wiki.probe import check_ingest_allowed

    wiki_gate = check_ingest_allowed()
    if wiki_gate.get("blocked"):
        logger.error(
            "hub news ingest blocked: %s (%s)",
            wiki_gate.get("reason"),
            wiki_gate.get("detail"),
        )
        return {
            "ticker": ticker.strip().upper(),
            "mode": (mode or "full").strip().lower(),
            "blocked": True,
            "pipeline_paused": True,
            "pause_reason": str(wiki_gate.get("reason") or "llm_wiki_unavailable"),
            "user_message": str(wiki_gate.get("user_message") or ""),
            "llm_wiki": wiki_gate.get("llm_wiki") or {},
            "sources": {},
            "totals": {"queued": 0, "ingested": 0, "verified": 0, "created": 0, "updated": 0, "error": 1},
            "finished_at": datetime.now(timezone.utc).isoformat(),
        }

    cfg = load_news_pipeline_config()
    sym = ticker.strip().upper()
    ingest_mode = (mode or "full").strip().lower()

    if ingest_mode == "light":
        market_context = get_market_context_for_pipeline(ticker=sym, refresh=False)
    else:
        market_context = refresh_index_market_context(ticker=sym, persist=True)

    if sources is None or sources == "default":
        if ingest_mode == "light":
            sources = cfg.light_ingest_sources
            if lookback_days is None:
                lookback_days = cfg.light_lookback_days
        else:
            sources = cfg.full_ingest_sources
            if lookback_days is None:
                lookback_days = cfg.full_lookback_days

    days = lookback_days
    if days is None:
        try:
            days = int(os.getenv("HUB_NEWS_INGEST_LOOKBACK_DAYS", "3"))
        except ValueError:
            days = 3

    selected = _apply_light_source_guard(_parse_sources(sources), ingest_mode=ingest_mode)
    sync_distill_limit = _sync_distill_limit_for_mode(ingest_mode)
    out: dict[str, Any] = {
        "ticker": sym,
        "mode": ingest_mode,
        "lookback_days": days,
        "sync_distill_limit": sync_distill_limit,
        "market_context": {
            "as_of": market_context.get("as_of"),
            "quotes_ok": market_context.get("quotes_ok"),
            "factors_ok": market_context.get("factors_ok"),
            "source": market_context.get("source"),
        },
        "sources_requested": sorted(selected),
        "sources": {},
        "totals": {"queued": 0, "ingested": 0, "verified": 0, "created": 0, "updated": 0, "error": 0},
        "finished_at": datetime.now(timezone.utc).isoformat(),
    }

    if "rss" in selected:
        try:
            _merge_stats(
                out,
                "rss",
                _ingest_rss(
                    ticker=sym,
                    limit_per_feed=rss_limit_per_feed,
                    sync_distill_limit=sync_distill_limit,
                ),
            )
        except Exception as exc:
            logger.warning("hub ingest rss failed: %s", exc)
            out["sources"]["rss"] = {"error": str(exc)[:200]}
            out["totals"]["error"] += 1
        check_pipeline_cancel()

    if "searxng" in selected:
        try:
            _merge_stats(
                out,
                "searxng",
                _ingest_searxng_ticker(
                    ticker=sym,
                    lookback_days=days,
                    sync_distill_limit=sync_distill_limit,
                ),
            )
        except Exception as exc:
            logger.warning("hub ingest searxng ticker failed: %s", exc)
            out["sources"]["searxng"] = {"error": str(exc)[:200]}
            out["totals"]["error"] += 1
        check_pipeline_cancel()

    if "searxng_global" in selected:
        try:
            _merge_stats(
                out,
                "searxng_global",
                _ingest_searxng_global(
                    lookback_days=days,
                    sync_distill_limit=sync_distill_limit,
                ),
            )
        except Exception as exc:
            logger.warning("hub ingest searxng global failed: %s", exc)
            out["sources"]["searxng_global"] = {"error": str(exc)[:200]}
            out["totals"]["error"] += 1
        check_pipeline_cancel()

    if "moneycontrol" in selected:
        try:
            _merge_stats(
                out,
                "moneycontrol",
                _ingest_moneycontrol_macro(
                    ticker=sym,
                    sync_distill_limit=sync_distill_limit,
                ),
            )
        except Exception as exc:
            logger.warning("hub ingest moneycontrol failed: %s", exc)
            out["sources"]["moneycontrol"] = {"error": str(exc)[:200]}
            out["totals"]["error"] += 1
        check_pipeline_cancel()

    if "watcher" in selected:
        watch_syms = watcher_tickers or ["NIFTY", "BANKNIFTY"]
        try:
            _merge_stats(
                out,
                "watcher",
                _ingest_watcher(
                    tickers=watch_syms,
                    since_hours=watcher_since_hours,
                    sync_distill_limit=sync_distill_limit,
                ),
            )
        except Exception as exc:
            logger.warning("hub ingest watcher failed: %s", exc)
            out["sources"]["watcher"] = {"error": str(exc)[:200]}
            out["totals"]["error"] += 1

    try:
        from trade_integrations.dataflows.news_hub_bridge import hub_news_pipeline_status

        out["pipeline"] = hub_news_pipeline_status(ticker=sym)
    except Exception as exc:
        out["pipeline"] = {"error": str(exc)[:200]}

    try:
        from trade_integrations.observability.hooks import emit_ingest_complete

        emit_ingest_complete(
            ticker=sym,
            mode=ingest_mode,
            sources=out.get("sources") if isinstance(out.get("sources"), dict) else {},
            totals=out.get("totals") if isinstance(out.get("totals"), dict) else {},
            blocked=bool(out.get("blocked")),
        )
    except ImportError:
        pass

    return out
