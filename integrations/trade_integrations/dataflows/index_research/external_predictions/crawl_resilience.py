"""Crawl failure detection and URL ordering for external predictions."""

from __future__ import annotations

import os
import re
from urllib.parse import urlparse

from trade_integrations.dataflows.crawl4ai_client import CrawlPageResult

_BOT_BLOCK_RE = re.compile(
    r"anti-bot|akamai|bot protection|access denied|captcha|cloudflare|blocked by",
    re.I,
)
_AKAMAI_WRAP_MARKERS = (
    "REDIRECT_QUERY_STRING",
    "REQUEST_URI] => /europe/",
    "[QUERY_STRING] => url=",
)

_DEFAULT_CRAWL_BLOCKLIST = frozenset({"moneycontrol.com"})
_MIN_USABLE_MARKDOWN_CHARS = 80


def crawl_blocklist_domains() -> frozenset[str]:
    raw = os.environ.get("EXTERNAL_PREDICTIONS_CRAWL_BLOCKLIST", "").strip()
    domains = set(_DEFAULT_CRAWL_BLOCKLIST)
    if raw:
        for part in raw.split(","):
            d = part.strip().lower().removeprefix("www.")
            if d:
                domains.add(d)
    return frozenset(domains)


def is_bot_block_error(message: str | None) -> bool:
    text = str(message or "").strip()
    if not text:
        return False
    return bool(_BOT_BLOCK_RE.search(text))


def is_akamai_wrapped_markdown(markdown: str | None, url: str = "") -> bool:
    """Detect Akamai geo-edge shells that return HTTP 200 with the wrong page body."""
    text = str(markdown or "").strip()
    if not text:
        return False
    if any(marker in text for marker in _AKAMAI_WRAP_MARKERS):
        return True
    target = str(url or "").strip()
    if target and "redirect_url =" in text and target in text:
        lowered = text.lower()
        if "/tags/" in target or target.rstrip("/").endswith("/markets"):
            if lowered.count("articleshow") == 0 and len(text) < 12000:
                return True
    return False


def is_crawl_bot_blocked(row: CrawlPageResult, url: str = "") -> bool:
    """True for explicit bot errors or Akamai-wrapped successful crawls."""
    if is_bot_block_error(row.error_message):
        return True
    if row.success and is_akamai_wrapped_markdown(row.markdown, url or row.url):
        return True
    return False


def crawl_rows_all_bot_blocked(rows: list[tuple[str, CrawlPageResult]]) -> bool:
    """True when every failed crawl row looks like anti-bot blocking."""
    failures = [
        (url, row)
        for url, row in rows
        if not row.success
        or not (row.markdown or "").strip()
        or is_akamai_wrapped_markdown(row.markdown, url)
    ]
    if not failures:
        return False
    blocked = [row for _, row in failures if is_crawl_bot_blocked(row)]
    return len(blocked) == len(failures)


def crawl_rows_any_bot_blocked(rows: list[tuple[str, CrawlPageResult]]) -> bool:
    """True when at least one failed row looks like anti-bot blocking."""
    for url, row in rows:
        if row.success and (row.markdown or "").strip() and not is_akamai_wrapped_markdown(row.markdown, url):
            continue
        if is_crawl_bot_blocked(row, url):
            return True
    return False


def crawl_rows_have_usable_text(rows: list[tuple[str, CrawlPageResult]]) -> bool:
    """True when crawl returned markdown but pick_best rejected it (no forecast signal)."""
    for url, row in rows:
        if row.success and len((row.markdown or "").strip()) >= _MIN_USABLE_MARKDOWN_CHARS:
            if not is_akamai_wrapped_markdown(row.markdown, url):
                return True
    return False


def crawl_rows_all_failed(rows: list[tuple[str, CrawlPageResult]]) -> bool:
    """True when every crawl row failed without usable markdown."""
    if not rows:
        return False
    return all(not row.success or not (row.markdown or "").strip() for _, row in rows)


def crawl_rows_success_without_usable_text(rows: list[tuple[str, CrawlPageResult]]) -> bool:
    """True when crawl succeeded but markdown is too short for forecast extraction."""
    if not any(row.success for _, row in rows):
        return False
    return not crawl_rows_have_usable_text(rows)


def should_run_searxng_fallback(
    rows: list[tuple[str, CrawlPageResult]],
    message: str,
) -> tuple[bool, str]:
    """
    Decide whether to attempt SearXNG text fallback after pick_best returns None.

    Returns (run_fallback, trigger_reason).
    """
    if not rows:
        return False, ""
    if crawl_rows_all_bot_blocked(rows):
        return True, "bot_all"
    if is_bot_block_error(message):
        return True, "bot_message"
    if crawl_rows_any_bot_blocked(rows):
        return True, "bot_any"
    if crawl_rows_have_usable_text(rows):
        return True, "crawl_no_forecast"
    if crawl_rows_success_without_usable_text(rows):
        return True, "crawl_no_forecast"
    if crawl_rows_all_failed(rows):
        return True, "crawl_all_failed"
    return False, ""


def url_host_domain(url: str) -> str:
    return (urlparse(url).hostname or "").lower().removeprefix("www.")


def is_blocklisted_crawl_domain(url: str) -> bool:
    host = url_host_domain(url)
    if not host:
        return False
    blocklist = crawl_blocklist_domains()
    return host in blocklist or any(host.endswith(f".{d}") for d in blocklist)


def sort_urls_for_crawl(urls: list[str]) -> list[str]:
    """Prefer non-blocklisted domains first; preserve order within each tier."""
    preferred: list[str] = []
    blocked: list[str] = []
    for url in urls:
        if is_blocklisted_crawl_domain(url):
            blocked.append(url)
        else:
            preferred.append(url)
    return preferred + blocked
