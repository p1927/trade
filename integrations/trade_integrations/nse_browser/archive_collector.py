"""Collect NSE archive CSV links from multiple pages."""

from __future__ import annotations

import logging
from typing import Any

from trade_integrations.nse_browser.dom_extract import collect_nsearchives_csv_links
from trade_integrations.nse_browser.nse_urls import (
    NSE_BULK_BLOCK_ARCHIVES,
    NSE_DELIVERY_ARCHIVES,
    NSE_HISTORICAL_REPORTS,
    NSE_PE_PB_PAGE,
)
from trade_integrations.nse_browser.registry import ARCHIVE_DATASETS
from trade_integrations.nse_browser.session import NodriverSession

logger = logging.getLogger(__name__)

_DATASET_PAGES: dict[str, tuple[str, ...]] = {
    "bulk_deals": (NSE_HISTORICAL_REPORTS[0], NSE_BULK_BLOCK_ARCHIVES),
    "delivery": (NSE_HISTORICAL_REPORTS[0], NSE_DELIVERY_ARCHIVES),
    "pe_pb": (NSE_HISTORICAL_REPORTS[0], NSE_PE_PB_PAGE),
}


def _link_matches_dataset(url: str, keywords: tuple[str, ...]) -> bool:
    lower = url.lower()
    if ".csv" not in lower and "download" not in lower:
        return False
    return any(kw in lower for kw in keywords)


async def collect_archive_links_for_dataset(
    session: NodriverSession,
    dataset: str,
) -> list[str]:
    """Visit archive pages once and harvest nsearchives CSV URLs."""
    meta = ARCHIVE_DATASETS.get(dataset) or {}
    keywords = tuple(kw.lower() for kw in (meta.get("keywords") or ()))
    pages = _DATASET_PAGES.get(dataset, (NSE_HISTORICAL_REPORTS[0],))
    seen: set[str] = set()
    out: list[str] = []

    for page_url in pages:
        try:
            await session.goto(page_url)
        except Exception as exc:
            logger.debug("archive page goto failed %s: %s", page_url[:60], exc)
            continue
        if session.captcha_detected and not session.captcha_resolved:
            continue

        for href in await collect_nsearchives_csv_links(session.tab):
            if href not in seen and _link_matches_dataset(href, keywords):
                seen.add(href)
                out.append(href)

        for href in await session.find_links_by_keywords(keywords):
            if href not in seen:
                seen.add(href)
                out.append(href)

        for href in await session.find_csv_hrefs():
            if href not in seen and _link_matches_dataset(href, keywords):
                seen.add(href)
                out.append(href)

    return out


async def collect_all_archive_links(session: NodriverSession) -> dict[str, list[str]]:
    """Single browser session — collect links for all archive datasets."""
    result: dict[str, list[str]] = {}
    await session.goto(NSE_HISTORICAL_REPORTS[0])
    hub_links = await collect_nsearchives_csv_links(session.tab)
    hub_links += await session.find_csv_hrefs()

    for dataset, meta in ARCHIVE_DATASETS.items():
        keywords = tuple(kw.lower() for kw in (meta.get("keywords") or ()))
        matched = [u for u in hub_links if _link_matches_dataset(u, keywords)]
        if not matched:
            matched = await collect_archive_links_for_dataset(session, dataset)
        result[dataset] = list(dict.fromkeys(matched))[:20]
    return result
