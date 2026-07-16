"""Public API for verified news — the only module to import for news.

All fetchers (aggregator, RSS, SearXNG, index pipeline, material watcher) ingest
through this package. All consumers (agents, API, analysis, UI) read through it.

See ``docs/news-hub-bridge.md`` for the full contract.

Do **not** import ``news_impact_engine``, ``news_collect``, or
``verified_news_store`` directly from application code — use this package.
"""

from __future__ import annotations

from typing import Any

from trade_integrations.dataflows.news_hub_bridge._ingest import (
    enrich_articles_with_hub_tags,
    hub_ticker_for_symbol,
    ingest_news_articles,
    ingest_rows_to_hub,
    ingest_rss_entries,
    ingest_searxng_results,
)

__all__ = [
    "hub_ticker_for_symbol",
    "ingest_rows_to_hub",
    "ingest_news_articles",
    "ingest_rss_entries",
    "ingest_searxng_results",
    "enrich_articles_with_hub_tags",
    "headlines_for_day",
    "headlines_for_prediction_date",
    "to_headline_dict",
    "list_headlines_for_date",
    "list_recent_headlines",
    "query_verified_news",
    "resolve_news_impact",
    "load_news_impact",
    "refresh_news_impact",
    "sync_news_impact_to_index_doc",
    "save_news_impact",
    "tag_inventory",
]


def headlines_for_day(
    day: str,
    *,
    ticker: str = "NIFTY",
    limit: int = 6,
    ingest_if_missing: bool = True,
    horizon_days: int = 14,
    spot: float | None = None,
    macro_factors: dict[str, float] | None = None,
) -> list[dict[str, Any]]:
    """Tagged verified headlines for a calendar day (hub SSOT)."""
    from trade_integrations.dataflows.index_research.news_impact_engine import headlines_for_day as _fn

    return _fn(
        day,
        ticker=ticker,
        limit=limit,
        ingest_if_missing=ingest_if_missing,
        horizon_days=horizon_days,
        spot=spot,
        macro_factors=macro_factors,
    )


def headlines_for_prediction_date(
    prediction_date: str,
    *,
    ticker: str = "NIFTY",
    lookback_days: int = 7,
    limit: int = 12,
    ingest_if_missing: bool = True,
    horizon_days: int = 14,
) -> list[dict[str, Any]]:
    """Headlines knowable at prediction time (publish_day <= prediction_date)."""
    from trade_integrations.dataflows.index_research.news_impact_engine import (
        headlines_for_prediction_date as _fn,
    )

    return _fn(
        prediction_date,
        ticker=ticker,
        lookback_days=lookback_days,
        limit=limit,
        ingest_if_missing=ingest_if_missing,
        horizon_days=horizon_days,
    )


def to_headline_dict(item: dict[str, Any]) -> dict[str, Any]:
    """Normalize a hub record for attribution / miss-analysis consumers."""
    from trade_integrations.dataflows.index_research.news_impact_engine import to_headline_dict as _fn

    return _fn(item)


def list_headlines_for_date(day: str, *, ticker: str = "NIFTY", limit: int = 12) -> list[dict[str, Any]]:
    """Approved/partial hub headlines for one publish day."""
    from trade_integrations.dataflows.index_research.news_impact_engine import list_approved_for_date

    return list_approved_for_date(day, ticker=ticker, limit=limit)


def list_recent_headlines(*, ticker: str = "NIFTY", limit: int = 12) -> list[dict[str, Any]]:
    """Recent verified headlines regardless of calendar day."""
    from trade_integrations.dataflows.index_research.news_impact_engine import list_recent_verified_headlines

    return list_recent_verified_headlines(ticker=ticker, limit=limit)


def query_verified_news(
    *,
    ticker: str = "NIFTY",
    status: str | list[str] | None = None,
    since: str | None = None,
    until: str | None = None,
    publish_day: str | None = None,
    topics: list[str] | None = None,
    factors: list[str] | None = None,
    themes: list[str] | None = None,
    tags: list[str] | None = None,
    limit: int = 50,
    include_rejected: bool = False,
) -> list[dict[str, Any]]:
    """Filter hub SSOT records by date and tags."""
    from trade_integrations.hub_storage.verified_news_store import list_verified_records

    return list_verified_records(
        ticker=ticker,
        status=status,
        since=since,
        until=until,
        publish_day=publish_day,
        topics=topics,
        factors=factors,
        themes=themes,
        tags=tags,
        limit=limit,
        include_rejected=include_rejected,
    )


def resolve_news_impact(
    *,
    ticker: str = "NIFTY",
    doc: Any | None = None,
    limit: int = 12,
    hydrate_from_hub: bool = True,
) -> dict[str, Any]:
    """Unified news_impact snapshot: doc → snapshot file → hub records."""
    from trade_integrations.dataflows.index_research.news_impact_engine import resolve_news_impact as _fn

    return _fn(ticker=ticker, doc=doc, limit=limit, hydrate_from_hub=hydrate_from_hub)


def load_news_impact(ticker: str = "NIFTY") -> dict[str, Any] | None:
    """Load cached ``news_impact_latest.json`` for a ticker."""
    from trade_integrations.dataflows.index_research.news_impact_engine import load_news_impact_snapshot

    return load_news_impact_snapshot(ticker)


def save_news_impact(report: dict[str, Any], *, ticker: str = "NIFTY") -> Any:
    """Persist a news_impact snapshot file under hub index research."""
    from trade_integrations.dataflows.index_research.news_impact_engine import save_news_impact_snapshot

    return save_news_impact_snapshot(report, ticker=ticker)


def refresh_news_impact(
    *,
    ticker: str = "NIFTY",
    horizon_days: int = 14,
    spot: float | None = None,
    macro_factors: dict[str, float] | None = None,
    refresh_ingest: bool = True,
    include_rejected: bool = False,
    headline_limit: int = 12,
) -> dict[str, Any]:
    """Ingest cache misses, build snapshot from hub, and save."""
    from trade_integrations.dataflows.index_research.news_impact_engine import build_news_impact_snapshot

    report = build_news_impact_snapshot(
        ticker=ticker,
        horizon_days=horizon_days,
        spot=spot,
        macro_factors=macro_factors,
        headline_limit=headline_limit,
        refresh_ingest=refresh_ingest,
        include_rejected=include_rejected,
    )
    save_news_impact(report, ticker=ticker)
    return report


def sync_news_impact_to_index_doc(doc: Any) -> dict[str, Any]:
    """Resolve news_impact from hub and persist snapshot for an index research doc."""
    from trade_integrations.dataflows.index_research.news_impact_engine import sync_news_impact_to_index_doc as _fn

    return _fn(doc)


def tag_inventory(*, ticker: str = "NIFTY") -> dict[str, Any]:
    """Summarize tag vocab used in hub for filter UIs."""
    from trade_integrations.hub_storage.verified_news_store import list_tag_inventory

    return list_tag_inventory(ticker=ticker)
