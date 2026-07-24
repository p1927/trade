"""Deterministic blocked-page detection before vision navigation escalation."""

from __future__ import annotations

import base64
import re
from dataclasses import dataclass

from trade_integrations.dataflows.crawl4ai_client import CrawlPageResult
from trade_integrations.dataflows.index_research.external_predictions.crawl4ai_fetcher import (
    keyword_match_score,
)
from trade_integrations.dataflows.index_research.external_predictions.crawl_resilience import (
    is_akamai_wrapped_markdown,
    is_bot_block_error,
    is_crawl_bot_blocked,
)
from trade_integrations.dataflows.index_research.external_predictions.url_policy import (
    is_allowed_listing_url,
    link_has_forecast_signal,
    markdown_has_nifty50_forecast,
)

# Pipeline log reason codes (Phase 1 v1).
BLOCK_REASON_COOKIE_BANNER = "cookie_banner"
BLOCK_REASON_NOTIFICATION_MODAL = "notification_modal"
BLOCK_REASON_THIN_MARKDOWN = "thin_markdown"
BLOCK_REASON_FOOTER_ONLY = "footer_only"
BLOCK_REASON_BOT_BLOCKED = "bot_blocked"
BLOCK_REASON_VISION_COOKIE_SCREENSHOT = "vision_cookie_screenshot"

BLOCK_REASONS: tuple[str, ...] = (
    BLOCK_REASON_COOKIE_BANNER,
    BLOCK_REASON_NOTIFICATION_MODAL,
    BLOCK_REASON_THIN_MARKDOWN,
    BLOCK_REASON_FOOTER_ONLY,
    BLOCK_REASON_BOT_BLOCKED,
    BLOCK_REASON_VISION_COOKIE_SCREENSHOT,
)

_THIN_MARKDOWN_CHARS = 1200
_COOKIE_SCAN_BYTES = 2048
_VISION_SCREENSHOT_MIN_BYTES = 50 * 1024
_ARTICLESHOW_RE = re.compile(r"articleshow", re.I)
_ARTICLE_H1_RE = re.compile(r"^#\s+.+", re.M)

_COOKIE_BANNER_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"onetrust", re.I),
    re.compile(r"we value your privacy", re.I),
    re.compile(r"accept all", re.I),
    re.compile(r"\bcookie", re.I),
)

_NOTIFICATION_MODAL_MARKERS: tuple[str, ...] = (
    "Get Top News alerts",
    "Maybe Later",
    "Enable",
)


@dataclass
class PageBlockSignal:
    blocked: bool
    reasons: list[str]
    confidence: float  # 0-1


_REASON_CONFIDENCE: dict[str, float] = {
    BLOCK_REASON_BOT_BLOCKED: 0.95,
    BLOCK_REASON_COOKIE_BANNER: 0.85,
    BLOCK_REASON_NOTIFICATION_MODAL: 0.85,
    BLOCK_REASON_FOOTER_ONLY: 0.80,
    BLOCK_REASON_VISION_COOKIE_SCREENSHOT: 0.75,
    BLOCK_REASON_THIN_MARKDOWN: 0.70,
}


def _scan_head(text: str, *, max_bytes: int = _COOKIE_SCAN_BYTES) -> str:
    raw = (text or "").encode("utf-8", errors="ignore")[:max_bytes]
    return raw.decode("utf-8", errors="ignore")


def _detect_cookie_banner(*, markdown: str, title: str) -> bool:
    head = _scan_head(f"{title}\n{markdown}")
    return any(pattern.search(head) for pattern in _COOKIE_BANNER_PATTERNS)


def _detect_notification_modal(markdown: str) -> bool:
    text = markdown or ""
    return any(marker in text for marker in _NOTIFICATION_MODAL_MARKERS)


def _detect_thin_markdown(*, url: str, markdown: str) -> bool:
    text = (markdown or "").strip()
    if len(text) >= _THIN_MARKDOWN_CHARS:
        return False
    if not is_allowed_listing_url(url).allowed:
        return False
    if keyword_match_score(text) > 0.0:
        return False
    if link_has_forecast_signal(text):
        return False
    if markdown_has_nifty50_forecast(text):
        return False
    return True


def _detect_footer_only(markdown: str) -> bool:
    text = markdown or ""
    if "Latest News" not in text or "Copyright © Bennett" not in text:
        return False
    first_lines = "\n".join(text.splitlines()[:40])
    if _ARTICLESHOW_RE.search(first_lines):
        return False
    if _ARTICLE_H1_RE.search(first_lines):
        return False
    return True


def _detect_bot_blocked(*, url: str, markdown: str, title: str) -> bool:
    if is_akamai_wrapped_markdown(markdown, url):
        return True
    if is_bot_block_error(title) or is_bot_block_error(markdown[:500]):
        return True
    row = CrawlPageResult(url=url, success=True, markdown=markdown or "", title=title)
    return is_crawl_bot_blocked(row, url)


def _detect_vision_cookie_screenshot(*, markdown: str, screenshot_b64: str | None) -> bool:
    if not screenshot_b64:
        return False
    text = (markdown or "").strip()
    if len(text) >= _THIN_MARKDOWN_CHARS:
        return False
    try:
        payload = screenshot_b64.split(",", 1)[-1]
        raw = base64.b64decode(payload, validate=False)
    except (ValueError, TypeError):
        return False
    return len(raw) > _VISION_SCREENSHOT_MIN_BYTES


def _signal_confidence(reasons: list[str]) -> float:
    if not reasons:
        return 0.0
    return max(_REASON_CONFIDENCE.get(reason, 0.5) for reason in reasons)


def detect_blocked_page(
    *,
    url: str,
    markdown: str,
    screenshot_b64: str | None = None,
    title: str = "",
) -> PageBlockSignal:
    """Return deterministic block signals for vision navigation escalation."""
    reasons: list[str] = []

    if _detect_bot_blocked(url=url, markdown=markdown, title=title):
        reasons.append(BLOCK_REASON_BOT_BLOCKED)
    if _detect_cookie_banner(markdown=markdown, title=title):
        reasons.append(BLOCK_REASON_COOKIE_BANNER)
    if _detect_notification_modal(markdown):
        reasons.append(BLOCK_REASON_NOTIFICATION_MODAL)
    if _detect_thin_markdown(url=url, markdown=markdown):
        reasons.append(BLOCK_REASON_THIN_MARKDOWN)
    if _detect_footer_only(markdown):
        reasons.append(BLOCK_REASON_FOOTER_ONLY)
    if _detect_vision_cookie_screenshot(markdown=markdown, screenshot_b64=screenshot_b64):
        reasons.append(BLOCK_REASON_VISION_COOKIE_SCREENSHOT)

    blocked = bool(reasons)
    return PageBlockSignal(
        blocked=blocked,
        reasons=reasons,
        confidence=_signal_confidence(reasons),
    )
