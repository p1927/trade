"""SearXNG-based news search for ticker and macro headlines.

Uses a local or remote SearXNG instance (JSON API) instead of yfinance Search
or a paid news API. Base URL comes from ``stack/ports.yaml`` via ``SEARXNG_BASE_URL``.
"""
import logging
import os
from datetime import datetime
from email.utils import parsedate_to_datetime
from urllib.parse import urljoin

import requests
from dateutil.relativedelta import relativedelta

from tradingagents.dataflows.config import get_config
from tradingagents.dataflows.yfinance_news import _in_news_window

logger = logging.getLogger(__name__)

REQUEST_TIMEOUT = 30


def _default_base_url() -> str:
    from trade_integrations.stack_ports import searxng_base_url

    return searxng_base_url()


def _base_url() -> str:
    return os.environ.get("SEARXNG_BASE_URL", _default_base_url()).rstrip("/")


def _parse_pub_date(result: dict) -> datetime | None:
    for key in ("publishedDate", "pubdate"):
        raw = result.get(key)
        if not raw:
            continue
        try:
            if isinstance(raw, (int, float)):
                return datetime.fromtimestamp(raw)
            if isinstance(raw, str):
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    dt = parsedate_to_datetime(raw)
                except ValueError:
                    dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
                return dt.replace(tzinfo=None) if dt.tzinfo else dt
        except (ValueError, TypeError, OverflowError):
            continue
    return None


def _search(query: str, limit: int) -> list[dict]:
    url = urljoin(_base_url() + "/", "search")
    try:
        resp = requests.get(
            url,
            params={"q": query, "format": "json", "categories": "news"},
            timeout=REQUEST_TIMEOUT,
        )
        resp.raise_for_status()
        payload = resp.json()
    except requests.RequestException as exc:
        logger.warning("SearXNG search failed for %r: %s", query, exc)
        return []
    except ValueError as exc:
        logger.warning("SearXNG returned invalid JSON for %r: %s", query, exc)
        return []

    results = payload.get("results") or []
    return results[:limit]


def _format_results(
    results: list[dict],
    *,
    header: str,
    start_dt: datetime,
    end_dt: datetime,
    limit: int,
) -> str:
    news_str = ""
    kept = 0
    seen_titles: set[str] = set()

    for result in results:
        title = (result.get("title") or "").strip()
        if not title or title in seen_titles:
            continue
        seen_titles.add(title)

        pub_date = _parse_pub_date(result)
        if not _in_news_window(pub_date, start_dt, end_dt):
            continue

        content = (result.get("content") or "").strip()
        link = (result.get("url") or "").strip()
        engines = result.get("engines") or []
        source = ", ".join(engines) if engines else "SearXNG"

        news_str += f"### {title} (source: {source})\n"
        if content:
            news_str += f"{content}\n"
        if link:
            news_str += f"Link: {link}\n"
        news_str += "\n"
        kept += 1
        if kept >= limit:
            break

    if kept == 0:
        return (
            f"No news found for {header} between "
            f"{start_dt.strftime('%Y-%m-%d')} and {end_dt.strftime('%Y-%m-%d')}"
        )

    return (
        f"## {header}, from {start_dt.strftime('%Y-%m-%d')} "
        f"to {end_dt.strftime('%Y-%m-%d')}:\n\n{news_str}"
    )


def get_news_searxng(ticker: str, start_date: str, end_date: str) -> str:
    """Retrieve ticker news via SearXNG web search."""
    config = get_config()
    article_limit = config["news_article_limit"]
    start_dt = datetime.strptime(start_date, "%Y-%m-%d")
    end_dt = datetime.strptime(end_date, "%Y-%m-%d")

    query = f"{ticker} stock news"
    results = _search(query, article_limit * 2)
    if not results:
        return f"No news found for {ticker} via SearXNG"

    try:
        from trade_integrations.dataflows.news_hub_bridge import ingest_searxng_results

        ingest_searxng_results(results, ticker=ticker, collection_day=end_date)
    except Exception as exc:
        logger.debug("hub ingest searxng ticker skipped: %s", exc)

    return _format_results(
        results,
        header=f"{ticker} News",
        start_dt=start_dt,
        end_dt=end_dt,
        limit=article_limit,
    )


def get_global_news_searxng(
    curr_date: str,
    look_back_days: int | None = None,
    limit: int | None = None,
) -> str:
    """Retrieve macro/global headlines via SearXNG."""
    config = get_config()
    if look_back_days is None:
        look_back_days = config["global_news_lookback_days"]
    if limit is None:
        limit = config["global_news_article_limit"]

    curr_dt = datetime.strptime(curr_date, "%Y-%m-%d")
    start_dt = curr_dt - relativedelta(days=look_back_days)

    all_results: list[dict] = []
    seen_titles: set[str] = set()

    for query in config["global_news_queries"]:
        for result in _search(query, limit):
            title = (result.get("title") or "").strip()
            if title and title not in seen_titles:
                seen_titles.add(title)
                all_results.append(result)
        if len(all_results) >= limit:
            break

    if not all_results:
        return f"No global news found for {curr_date} via SearXNG"

    try:
        from trade_integrations.dataflows.news_hub_bridge import ingest_searxng_results

        ingest_searxng_results(all_results, ticker="NIFTY", kind="global", collection_day=curr_date)
    except Exception as exc:
        logger.debug("hub ingest searxng global skipped: %s", exc)

    return _format_results(
        all_results,
        header="Global Market News",
        start_dt=start_dt,
        end_dt=curr_dt,
        limit=limit,
    )
