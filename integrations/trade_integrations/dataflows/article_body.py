"""Fetch full article text for trusted India finance/news domains."""

from __future__ import annotations

import hashlib
import html
import json
import logging
import os
import re
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlparse

from trade_integrations.context.hub import get_hub_dir
from trade_integrations.dataflows.searxng_finance import TRUSTED_FINANCE_DOMAINS
from trade_integrations.http import RequestException, get

logger = logging.getLogger(__name__)

_EXTRA_TRUSTED_DOMAINS = (
    "economictimes.com",
    "hindustantimes.com",
    "financialexpress.com",
)

_TRUSTED_ARTICLE_DOMAINS = TRUSTED_FINANCE_DOMAINS + _EXTRA_TRUSTED_DOMAINS
_CACHE_DIR = "_data/article_cache"
_DEFAULT_MIN_SUMMARY_LEN = 400
_DEFAULT_MAX_CHARS = 8000
_DEFAULT_CACHE_DAYS = 7
_UA = "trade-stack-research/0.1 (+https://github.com/p1927/trade)"


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name, "1" if default else "0").strip().lower()
    return raw in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)).strip())
    except ValueError:
        return default


def article_fetch_enabled() -> bool:
    return _env_bool("HUB_NEWS_FETCH_ARTICLE_BODY", True)


def min_summary_len_for_fetch() -> int:
    return _env_int("HUB_NEWS_ARTICLE_MIN_SUMMARY_LEN", _DEFAULT_MIN_SUMMARY_LEN)


def max_article_chars() -> int:
    return _env_int("HUB_NEWS_ARTICLE_MAX_CHARS", _DEFAULT_MAX_CHARS)


def cache_ttl_days() -> int:
    return _env_int("HUB_NEWS_ARTICLE_CACHE_DAYS", _DEFAULT_CACHE_DAYS)


def _cache_dir() -> os.PathLike:
    path = get_hub_dir() / _CACHE_DIR
    path.mkdir(parents=True, exist_ok=True)
    return path


def _url_hash(url: str) -> str:
    return hashlib.sha256(url.strip().encode("utf-8")).hexdigest()


def _domain_allowed(url: str) -> bool:
    host = (urlparse(url).hostname or "").lower().removeprefix("www.")
    if not host:
        return False
    return any(host == domain or host.endswith(f".{domain}") for domain in _TRUSTED_ARTICLE_DOMAINS)


def _normalize_whitespace(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _extract_body_from_html(html_text: str) -> str:
    try:
        from bs4 import BeautifulSoup
    except ImportError:
        logger.debug("beautifulsoup4 unavailable for article extraction")
        return _normalize_whitespace(re.sub(r"<[^>]+>", " ", html_text))[:max_article_chars()]

    soup = BeautifulSoup(html_text, "html.parser")
    for tag in soup(["script", "style", "nav", "footer", "header", "aside", "noscript"]):
        tag.decompose()

    article = soup.find("article")
    if article is not None:
        text = article.get_text(separator=" ", strip=True)
        if len(text) >= 120:
            return _normalize_whitespace(text)[:max_article_chars()]

    main = soup.find("main")
    if main is not None:
        text = main.get_text(separator=" ", strip=True)
        if len(text) >= 120:
            return _normalize_whitespace(text)[:max_article_chars()]

    og_desc = soup.find("meta", attrs={"property": "og:description"})
    if og_desc and og_desc.get("content"):
        text = _normalize_whitespace(str(og_desc["content"]))
        if text:
            return text[:max_article_chars()]

    meta_desc = soup.find("meta", attrs={"name": "description"})
    if meta_desc and meta_desc.get("content"):
        text = _normalize_whitespace(str(meta_desc["content"]))
        if text:
            return text[:max_article_chars()]

    text = soup.get_text(separator=" ", strip=True)
    return _normalize_whitespace(text)[:max_article_chars()]


def _read_cache_payload(url: str) -> dict[str, Any] | None:
    path = _cache_dir() / f"{_url_hash(url)}.json"
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    fetched_at = str(payload.get("fetched_at") or "")
    if fetched_at:
        try:
            ts = datetime.fromisoformat(fetched_at.replace("Z", "+00:00"))
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            if datetime.now(timezone.utc) - ts > timedelta(days=cache_ttl_days()):
                return None
        except ValueError:
            pass
    body = str(payload.get("body") or "").strip()
    if not body:
        return None
    return payload


def _read_cache(url: str) -> str | None:
    payload = _read_cache_payload(url)
    if not payload:
        return None
    return str(payload.get("body") or "").strip() or None


def _read_cache_published_meta(url: str) -> str:
    payload = _read_cache_payload(url)
    if not payload:
        return ""
    return str(payload.get("published_meta") or "").strip()


def _write_cache(url: str, body: str, *, published_meta: str = "") -> None:
    path = _cache_dir() / f"{_url_hash(url)}.json"
    payload = {
        "url": url,
        "body": body[:max_article_chars()],
        "fetched_at": datetime.now(timezone.utc).isoformat(),
    }
    meta = (published_meta or "").strip()
    if meta:
        payload["published_meta"] = meta[:80]
    try:
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    except OSError as exc:
        logger.debug("article cache write failed for %s: %s", url[:80], exc)


def fetch_article_body(
    url: str,
    *,
    min_existing_summary_len: int | None = None,
) -> str | None:
    """Fetch and extract article text for trusted domains; None if skip/fail."""
    if not article_fetch_enabled():
        return None
    link = (url or "").strip()
    if not link.startswith(("http://", "https://")):
        return None
    if not _domain_allowed(link):
        return None

    cached = _read_cache(link)
    if cached:
        return cached

    try:
        resp = get(link, headers={"User-Agent": _UA}, timeout=15.0)
        resp.raise_for_status()
    except RequestException as exc:
        logger.debug("article fetch failed for %s: %s", link[:80], exc)
        return None

    encoding = resp.encoding or resp.apparent_encoding or "utf-8"
    try:
        html_text = resp.content.decode(encoding, errors="replace")
    except LookupError:
        html_text = resp.text

    body = _extract_body_from_html(html.unescape(html_text))
    if len(body) < 120:
        logger.debug("article body too short for %s", link[:80])
        return None

    _write_cache(link, body)
    return body


def fetch_article_body_with_html(url: str) -> tuple[str | None, str]:
    """Fetch article text and raw HTML for trusted domains; (None, '') on skip/fail."""
    if not article_fetch_enabled():
        return None, ""
    link = (url or "").strip()
    if not link.startswith(("http://", "https://")):
        return None, ""
    if not _domain_allowed(link):
        return None, ""

    cached = _read_cache_payload(link)
    if cached:
        meta = str(cached.get("published_meta") or "").strip()
        return str(cached.get("body") or "").strip() or None, meta

    try:
        resp = get(link, headers={"User-Agent": _UA}, timeout=15.0)
        resp.raise_for_status()
    except RequestException as exc:
        logger.debug("article fetch failed for %s: %s", link[:80], exc)
        return None, ""

    encoding = resp.encoding or resp.apparent_encoding or "utf-8"
    try:
        html_text = resp.content.decode(encoding, errors="replace")
    except LookupError:
        html_text = resp.text

    from trade_integrations.dataflows.index_research.hub_news_pipeline.step_03_datetime_normalize import (
        extract_published_meta_from_html,
    )

    published_meta = extract_published_meta_from_html(html.unescape(html_text))
    body = _extract_body_from_html(html.unescape(html_text))
    if len(body) < 120:
        logger.debug("article body too short for %s", link[:80])
        return None, html_text

    _write_cache(link, body, published_meta=published_meta)
    return body, html_text


def enrich_ref_summary_from_url(ref: dict[str, Any]) -> dict[str, Any]:
    """Mutate ref summary from fetched article body when snippet is thin."""
    url = str(ref.get("url") or "").strip()
    summary = str(ref.get("summary") or "").strip()
    if not url:
        return ref
    if len(summary) >= min_summary_len_for_fetch():
        return ref

    body = fetch_article_body(url, min_existing_summary_len=len(summary))
    if not body:
        return ref

    title = str(ref.get("title") or "").strip()
    if title and body.lower().startswith(title.lower()):
        merged = body
    elif summary and summary.lower() not in body.lower():
        merged = f"{summary}\n\n{body}"
    else:
        merged = body

    ref["summary"] = merged[:2000]
    return ref
