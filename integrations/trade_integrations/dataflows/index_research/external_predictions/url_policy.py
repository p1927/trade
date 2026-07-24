"""URL guardrails for NIFTY 50 index forecast crawling."""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urlparse

_DENY_PATH = re.compile(
    r"/(?:career|careers|jobs?|options|futures|derivatives|mutual-fund|login|privacy|about|contact|"
    r"products|trading-features|stocks#)(?:/|$|[?#])|\.pdf(?:$|[?#])",
    re.I,
)
_MEDIA_PATH = re.compile(r"\.(?:png|jpe?g|gif|webp|svg|ico)(?:$|[?#])", re.I)
_NIFTY50_SIGNAL = re.compile(r"nifty\s*50|nifty50", re.I)
_FORECAST_SIGNAL = re.compile(
    r"target|forecast|outlook|projection|sees\s+nifty|nifty.*target|peg.*nifty",
    re.I,
)
_OPTIONS_SIGNAL = re.compile(
    r"\b(?:ce|pe|call|put|strike|expiry|option[\s-]?chain|f&o|fno)\b",
    re.I,
)

MIN_KEYWORD_SCORE = 4.0


@dataclass(frozen=True)
class UrlPolicyResult:
    allowed: bool
    reason: str = ""


def _has_nifty_index_signal(blob: str) -> bool:
    if _NIFTY50_SIGNAL.search(blob):
        return True
    return bool(re.search(r"\bnifty\b", blob, re.I) and _FORECAST_SIGNAL.search(blob))


def is_allowed_listing_url(url: str) -> UrlPolicyResult:
    """Allowlisted seed/listing pages — denylist only (no NIFTY-in-URL requirement)."""
    u = (url or "").strip()
    if not u:
        return UrlPolicyResult(False, "empty_url")
    path = urlparse(u).path.lower()
    if _DENY_PATH.search(path) or _DENY_PATH.search(u.lower()):
        return UrlPolicyResult(False, "deny_path")
    if _MEDIA_PATH.search(path) or _MEDIA_PATH.search(u.lower()):
        return UrlPolicyResult(False, "media_asset")
    if _OPTIONS_SIGNAL.search(u):
        return UrlPolicyResult(False, "options_content")
    return UrlPolicyResult(True, "ok")


def link_has_forecast_signal(blob: str) -> bool:
    """True when link title/URL text suggests a NIFTY index forecast page."""
    text = blob or ""
    if _has_nifty_index_signal(text):
        return True
    if re.search(r"\bnifty\b", text, re.I) and _FORECAST_SIGNAL.search(text):
        return True
    if re.search(r"\bnifty\b", text, re.I) and re.search(
        r"support|resistance|prediction|outlook",
        text,
        re.I,
    ):
        return True
    return False


def is_candidate_article_url(url: str, *, title: str = "") -> UrlPolicyResult:
    """Rankable forecast link — deny junk paths/media; require NIFTY forecast signal in title+URL."""
    u = (url or "").strip()
    if not u:
        return UrlPolicyResult(False, "empty_url")
    parsed = urlparse(u)
    path = (parsed.path or "").lower()
    blob = f"{title} {u}"

    if _DENY_PATH.search(path) or _DENY_PATH.search(u.lower()):
        return UrlPolicyResult(False, "deny_path")
    if _MEDIA_PATH.search(path) or _MEDIA_PATH.search(u.lower()):
        return UrlPolicyResult(False, "media_asset")
    if _OPTIONS_SIGNAL.search(blob):
        return UrlPolicyResult(False, "options_content")
    if not link_has_forecast_signal(blob):
        return UrlPolicyResult(False, "no_forecast_signal")
    return UrlPolicyResult(True, "ok")


def link_score(title: str, url: str, *, native_score: float | None = None) -> float:
    """Rank forecast link candidates — higher is more likely to contain a NIFTY 50 forecast."""
    blob = f"{title} {url}"
    score = 0.0
    if native_score is not None and native_score > 0:
        score += float(native_score) * 5.0
    if _NIFTY50_SIGNAL.search(blob) or re.search(r"\bnifty\b", blob, re.I):
        score += 2.0
    if _FORECAST_SIGNAL.search(blob):
        score += 1.5
    if re.search(r"support|resistance", blob, re.I) and re.search(r"\bnifty\b", blob, re.I):
        score += 1.0
    return score


def is_allowed_url(url: str, *, title: str = "") -> UrlPolicyResult:
    u = (url or "").strip()
    if not u:
        return UrlPolicyResult(False, "empty_url")
    parsed = urlparse(u)
    path = (parsed.path or "").lower()
    blob = f"{title} {u}"

    if _DENY_PATH.search(path) or _DENY_PATH.search(u.lower()):
        return UrlPolicyResult(False, "deny_path")
    if _OPTIONS_SIGNAL.search(blob):
        return UrlPolicyResult(False, "options_content")
    if not _has_nifty_index_signal(blob):
        return UrlPolicyResult(False, "no_nifty50_signal")
    if not _FORECAST_SIGNAL.search(blob):
        return UrlPolicyResult(False, "no_forecast_signal")
    return UrlPolicyResult(True, "ok")


_LINK_ONLY_LINE = re.compile(r"^\[.*\]\(.*\)\s*$")
_IMAGE_ONLY_LINE = re.compile(r"^!\[.*\]\(.*\)\s*$")


def markdown_prose_body(markdown: str) -> str:
    """Drop nav/link-only lines so forecast checks use page prose, not anchor text."""
    lines: list[str] = []
    for line in (markdown or "").splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if _IMAGE_ONLY_LINE.match(stripped) or _LINK_ONLY_LINE.match(stripped):
            continue
        lines.append(stripped)
    return "\n".join(lines)


def markdown_has_nifty50_forecast(markdown: str) -> bool:
    text = markdown_prose_body(markdown)
    if not _has_nifty_index_signal(text):
        return False
    if not re.search(r"target|forecast|outlook|projection|sees", text, re.I):
        return False
    if not re.search(r"\d{1,2}[,.]?\d{3,5}", text):
        return False
    return True


def is_article_url(url: str) -> bool:
    path = urlparse(url).path.lower()
    if "/articleshow/" in path or "/story/" in path:
        return True
    listing_hints = ("/news", "/market", "/markets", "/topic/", "/indices/")
    if any(h in path for h in listing_hints) and path.count("/") <= 4:
        return False
    return path.count("/") >= 4


def is_structured_forecast_hub_url(url: str) -> bool:
    path = urlparse(url).path.lower()
    if re.search(r"/market/nifty/?$", path):
        return True
    if "/blog/" in path and re.search(r"prediction|forecast|outlook", path, re.I):
        return True
    return False


def is_listing_page_url(url: str) -> bool:
    u = (url or "").strip()
    if not u:
        return False
    if is_structured_forecast_hub_url(u):
        return False
    if is_article_url(u):
        return False
    path = urlparse(u).path.lower()
    listing_hints = (
        "/topic/",
        "/markets/stocks/news",
        "/market/stock-market-news",
        "/news/tags/",
        "/indices/nifty",
    )
    if any(h in path for h in listing_hints):
        return True
    if path in {"/market", "/markets", "/news"}:
        return True
    return path.count("/") <= 3


def classify_page_kind(url: str) -> str:
    if is_structured_forecast_hub_url(url):
        return "hub"
    if is_article_url(url):
        return "article"
    if is_listing_page_url(url):
        return "listing"
    return "other"


def url_selection_penalty(url: str) -> float:
    """Negative adjustments for generic listing/topic pages in pick_best ranking."""
    path = urlparse(url).path.lower()
    penalty = 0.0
    if "/topic/" in path:
        penalty -= 4.0
    if "/markets/stocks/news" in path or path.rstrip("/") == "/market":
        penalty -= 3.0
    if "/news/tags/" in path:
        penalty -= 2.0
    if is_article_url(url):
        penalty += 2.0
    if is_structured_forecast_hub_url(url):
        penalty += 1.5
    return penalty
