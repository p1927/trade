"""SearXNG finance-category search against trusted India market portals."""

from __future__ import annotations

import logging
import re
import time
from typing import Any

from trade_integrations.http import RequestException

from trade_integrations.dataflows.searxng_client import (
    engine_unresponsive_reason,
    parse_engine_list,
    search_json,
    searxng_finance_engines,
    should_retry_engine_search,
)

logger = logging.getLogger(__name__)

REQUEST_TIMEOUT = 30
FINANCE_CATEGORY = "finance"

TRUSTED_FINANCE_DOMAINS = (
    "moneycontrol.com",
    "screener.in",
    "economictimes.indiatimes.com",
    "indiatimes.com",
    "niftyindices.com",
    "nseindia.com",
    "livemint.com",
    "business-standard.com",
    "rbi.org.in",
)

_PE_PATTERNS = (
    re.compile(r"(?:trailing\s*)?p\s*/\s*e[^0-9]{0,20}(\d{1,2}(?:\.\d{1,2})?)", re.I),
    re.compile(r"pe\s*ratio[^0-9]{0,20}(\d{1,2}(?:\.\d{1,2})?)", re.I),
    re.compile(r"nifty\s*50\s*pe[^0-9]{0,20}(\d{1,2}(?:\.\d{1,2})?)", re.I),
)

_REPO_PATTERNS = (
    re.compile(r"repo\s+rate[^0-9]{0,40}(\d+(?:\.\d+)?)\s*(?:%|per\s+cent)?", re.I),
    re.compile(r"policy\s+repo\s+rate[^0-9]{0,40}(\d+(?:\.\d+)?)", re.I),
    re.compile(r"unchanged\s+at\s+(\d+(?:\.\d+)?)\s*(?:%|per\s+cent)?", re.I),
)

_CPI_PATTERNS = (
    re.compile(r"(?:cpi|retail)\s+inflation[^0-9]{0,30}(\d+(?:\.\d+)?)\s*(?:%|per\s+cent)?", re.I),
    re.compile(r"inflation\s+(?:rate|at|stood\s+at)[^0-9]{0,20}(\d+(?:\.\d+)?)", re.I),
)

_NIFTY_PE_PATTERNS = _PE_PATTERNS + (
    re.compile(r"trailing\s*pe\s*(?:ratio|multiple)?[^0-9]{0,30}(\d+(?:\.\d+)?)", re.I),
    re.compile(r"pe\s*multiple[^0-9]{0,30}(\d+(?:\.\d+)?)", re.I),
)

_UNTRUSTED_PE_URL_FRAGMENTS = (
    "low-pe-trending",
    "ratio-scans",
    "/company/niftyjr",
    ".00 pe",
    "/options/",
)


def _trusted_result(result: dict[str, Any]) -> bool:
    url = str(result.get("url") or "").lower()
    return any(domain in url for domain in TRUSTED_FINANCE_DOMAINS)


def _trusted_nifty_pe_result(result: dict[str, Any]) -> bool:
    url = str(result.get("url") or "").lower()
    if any(fragment in url for fragment in _UNTRUSTED_PE_URL_FRAGMENTS):
        return False
    blob = " ".join(str(result.get(key) or "") for key in ("title", "content", "url")).lower()
    if "nifty next 50" in blob or "niftyjr" in url:
        return False
    if "nifty 50" in blob or "nifty-50" in url or "nifty50" in blob:
        return True
    return _trusted_result(result)


def search_finance(
    query: str,
    *,
    limit: int = 8,
    categories: str = FINANCE_CATEGORY,
) -> list[dict[str, Any]]:
    """Query SearXNG JSON API (finance category when configured)."""
    category_attempts = [categories, "general", "news"]
    engine_attempts = parse_engine_list(searxng_finance_engines()) or ["bing"]
    seen_urls: set[str] = set()
    collected: list[dict[str, Any]] = []

    for cat in category_attempts:
        for engine in engine_attempts:
            for attempt in range(2):
                try:
                    payload = search_json(
                        query,
                        categories=cat,
                        engines=engine,
                        timeout=REQUEST_TIMEOUT,
                    )
                except RequestException as exc:
                    logger.debug(
                        "SearXNG search failed (%s/%s) for %r: %s", cat or "all", engine, query, exc
                    )
                    break
                except ValueError as exc:
                    logger.debug(
                        "SearXNG invalid JSON (%s/%s) for %r: %s", cat or "all", engine, query, exc
                    )
                    break

                for row in payload.get("results") or []:
                    link = str(row.get("url") or "")
                    if not link or link in seen_urls:
                        continue
                    seen_urls.add(link)
                    if _trusted_result(row):
                        collected.append(row)
                    if len(collected) >= limit:
                        return collected[:limit]

                reason = engine_unresponsive_reason(payload, engine)
                if reason:
                    if should_retry_engine_search(payload, engine, attempt=attempt):
                        time.sleep(2.0)
                        continue
                    logger.warning(
                        "SearXNG engine unresponsive (%s/%s): %s for %r",
                        cat,
                        engine,
                        reason,
                        query,
                    )
                break

    return collected[:limit]


def parse_scalar_from_results(
    results: list[dict[str, Any]],
    *,
    patterns: tuple[re.Pattern[str], ...] = _PE_PATTERNS,
    min_value: float = 8.0,
    max_value: float = 80.0,
    required_terms: tuple[str, ...] = (),
) -> float | None:
    """Extract the first plausible numeric scalar from trusted snippets."""
    for result in results:
        blob = " ".join(
            str(result.get(key) or "")
            for key in ("title", "content", "url")
        )
        blob_lower = blob.lower()
        if required_terms and not all(term in blob_lower for term in required_terms):
            continue
        for pattern in patterns:
            match = pattern.search(blob)
            if not match:
                continue
            try:
                value = float(match.group(1))
            except (TypeError, ValueError):
                continue
            if min_value <= value <= max_value:
                return round(value, 4)
    return None


def parse_repo_rate_from_results(results: list[dict[str, Any]]) -> float | None:
    return parse_scalar_from_results(
        results,
        patterns=_REPO_PATTERNS,
        min_value=6.0,
        max_value=7.5,
        required_terms=("repo",),
    )


def parse_cpi_yoy_from_results(results: list[dict[str, Any]]) -> float | None:
    return parse_scalar_from_results(
        results,
        patterns=_CPI_PATTERNS,
        min_value=1.0,
        max_value=15.0,
        required_terms=("inflation",),
    )


def parse_nifty_pe_from_results(results: list[dict[str, Any]]) -> float | None:
    filtered = [row for row in results if _trusted_nifty_pe_result(row)]
    return parse_scalar_from_results(
        filtered,
        patterns=_NIFTY_PE_PATTERNS,
        min_value=12.0,
        max_value=35.0,
        required_terms=("nifty",),
    )


def fetch_rbi_macro_via_searxng() -> dict[str, Any]:
    """Best-effort RBI repo rate and CPI YoY from trusted finance portals."""
    repo_queries = (
        "RBI repo rate unchanged at 6.5 India moneycontrol livemint",
        "RBI MPC policy repo rate current India rbi.org.in",
        "RBI repo rate monetary policy India economictimes",
        "RBI repo rate current moneycontrol",
    )
    cpi_queries = (
        "India CPI inflation yoy moneycontrol retail",
        "India retail inflation livemint CPI",
        "India CPI inflation economictimes yoy",
    )

    repo_rate: float | None = None
    cpi_yoy: float | None = None
    metadata: dict[str, Any] = {}

    for query in repo_queries:
        results = search_finance(query, limit=6)
        repo_rate = parse_repo_rate_from_results(results)
        if repo_rate is not None:
            metadata["repo_query"] = query
            if results:
                metadata["repo_url"] = results[0].get("url")
            break

    for query in cpi_queries:
        results = search_finance(query, limit=6)
        cpi_yoy = parse_cpi_yoy_from_results(results)
        if cpi_yoy is not None:
            metadata["cpi_query"] = query
            if results:
                metadata["cpi_url"] = results[0].get("url")
            break

    if repo_rate is None and cpi_yoy is None:
        return {}

    payload: dict[str, Any] = {"source": "searxng_finance", "metadata": metadata}
    if repo_rate is not None:
        payload["repo_rate"] = repo_rate
    if cpi_yoy is not None:
        payload["cpi_yoy_proxy"] = cpi_yoy
    return payload


def fetch_nifty_trailing_pe_via_searxng() -> dict[str, Any] | None:
    """Best-effort Nifty trailing P/E from Moneycontrol / Screener / ET via SearXNG."""
    queries = (
        "Nifty 50 trailing PE ratio economictimes moneycontrol",
        "Nifty 50 PE ratio today moneycontrol India",
        "Nifty 50 PE ratio economictimes indiatimes",
        "Nifty 50 PE multiple trailing screener",
        "Nifty 50 index trailing PE ratio screener.in",
    )
    for query in queries:
        results = search_finance(query, limit=8)
        pe = parse_nifty_pe_from_results(results)
        if pe is None:
            continue
        top = results[0] if results else {}
        return {
            "value": pe,
            "source": "searxng_finance",
            "query": query,
            "url": top.get("url"),
            "engines": top.get("engines"),
        }
    return None
