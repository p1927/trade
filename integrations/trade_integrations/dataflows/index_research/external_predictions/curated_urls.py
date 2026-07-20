"""Curated crawl URLs for NIFTY 50 index street forecasts (allowlist per source)."""

from __future__ import annotations

from typing import Final

# Web-research-backed listing/topic pages that publish NIFTY 50 index level targets.
# Crawl4AI uses these instead of generic marketing hubs (careers, F&O, etc.).
CURATED_URLS_BY_SOURCE: Final[dict[str, tuple[str, ...]]] = {
    "moneycontrol": (
        "https://www.moneycontrol.com/news/tags/nifty.html",
        "https://www.moneycontrol.com/news/business/markets/",
    ),
    "economictimes": (
        "https://economictimes.indiatimes.com/topic/nifty-50",
        "https://economictimes.indiatimes.com/markets/indices/nifty-50",
        "https://economictimes.indiatimes.com/topic/goldman-sachs-nifty",
        "https://economictimes.indiatimes.com/topic/morgan-stanley-nifty",
        "https://economictimes.indiatimes.com/markets/stocks/news",
    ),
    "livemint": (
        "https://www.livemint.com/market/stock-market-news",
        "https://www.livemint.com/market",
    ),
    "motilal_oswal": (
        "https://economictimes.indiatimes.com/topic/nifty-50",
        "https://www.moneycontrol.com/news/tags/nifty.html",
    ),
    "icici_direct": (
        "https://economictimes.indiatimes.com/topic/nifty-50",
        "https://www.moneycontrol.com/news/tags/nifty.html",
    ),
    "hdfc_securities": (
        "https://economictimes.indiatimes.com/topic/nifty-50",
        "https://www.moneycontrol.com/news/tags/nifty.html",
    ),
    "goldman_sachs": (
        "https://economictimes.indiatimes.com/topic/goldman-sachs-nifty",
        "https://www.livemint.com/market/stock-market-news",
    ),
    "morgan_stanley": (
        "https://economictimes.indiatimes.com/topic/morgan-stanley-nifty",
        "https://www.livemint.com/market/stock-market-news",
    ),
}


def curated_urls_for_source(source_id: str) -> list[str]:
    key = (source_id or "").strip().lower()
    return list(CURATED_URLS_BY_SOURCE.get(key, ()))
