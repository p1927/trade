"""Unified source policy for company research.

Nifty 50 index batch (prediction constituent load): free sources only —
OpenAlgo, BSE, screener.in, yfinance, SearXNG. No Tapetide, Alpha Vantage,
or other quota-tiered APIs.

Single-stock / agent paths: tiered enrichment allowed when configured.
"""

from __future__ import annotations

from contextvars import ContextVar

_nifty50_batch: ContextVar[bool] = ContextVar("nifty50_batch", default=False)

TIERED_SOURCE_KEYS = frozenset({"tapetide", "alpha_vantage"})

# News backends safe for parallel Nifty 50 batch (no paid/rate-tier quotas).
NIFTY50_BATCH_NEWS_SOURCES: tuple[str, ...] = ("searxng",)


def set_nifty50_batch(active: bool) -> None:
    """Mark the current thread as Nifty 50 constituent batch research."""
    _nifty50_batch.set(active)


def is_nifty50_batch() -> bool:
    return _nifty50_batch.get()


def allow_tiered_apis() -> bool:
    """False during Nifty 50 batch — skip Tapetide, Alpha Vantage, etc."""
    return not is_nifty50_batch()


def tiered_source_allowed(source_key: str) -> bool:
    if source_key in TIERED_SOURCE_KEYS and not allow_tiered_apis():
        return False
    return True


def news_sources_for_batch() -> list[str] | None:
    """When in Nifty 50 batch, return fixed free news backends; else None (use config)."""
    if not is_nifty50_batch():
        return None
    return list(NIFTY50_BATCH_NEWS_SOURCES)
