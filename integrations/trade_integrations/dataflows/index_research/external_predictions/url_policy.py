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
    if _OPTIONS_SIGNAL.search(u):
        return UrlPolicyResult(False, "options_content")
    return UrlPolicyResult(True, "ok")


def is_candidate_article_url(url: str, *, title: str = "") -> UrlPolicyResult:
    """Loose article discovery — article-shaped paths on allowlisted domains, no NIFTY-in-title rule."""
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
    if not is_article_url(u):
        return UrlPolicyResult(False, "not_article")
    return UrlPolicyResult(True, "ok")


def link_score(title: str, url: str, *, native_score: float | None = None) -> float:
    """Rank article candidates — higher is more likely to contain a NIFTY 50 street forecast."""
    blob = f"{title} {url}"
    score = 0.0
    if native_score is not None and native_score > 0:
        score += float(native_score) * 5.0
    if _NIFTY50_SIGNAL.search(blob) or re.search(r"\bnifty\b", blob, re.I):
        score += 2.0
    if _FORECAST_SIGNAL.search(blob):
        score += 1.5
    if is_article_url(url):
        score += 3.0
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


def markdown_has_nifty50_forecast(markdown: str) -> bool:
    text = markdown or ""
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
