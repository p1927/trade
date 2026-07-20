"""Discover new external prediction source candidates via SearXNG."""

from __future__ import annotations

import logging
import re
from typing import Any
from urllib.parse import urlparse

from trade_integrations.dataflows.index_research.external_predictions.models import (
    ExternalPredictionSource,
)
from trade_integrations.dataflows.index_research.external_predictions.source_registry import (
    load_registry,
    merge_discovered_candidate,
)
from trade_integrations.dataflows.searxng_finance import search_finance

logger = logging.getLogger(__name__)

_DISCOVERY_QUERIES = (
    "Nifty 50 forecast outlook target analyst",
    "Nifty 50 target price brokerage report India",
    "Nifty 50 year end target global bank India",
)

_SKIP_DOMAINS = frozenset(
    {
        "youtube.com",
        "twitter.com",
        "x.com",
        "facebook.com",
        "instagram.com",
        "wikipedia.org",
        "reddit.com",
        "linkedin.com",
        "google.com",
        "bing.com",
    }
)


def _domain(url: str) -> str:
    host = (urlparse(url).hostname or "").lower().removeprefix("www.")
    return host


def _display_name_from_domain(domain: str) -> str:
    base = domain.split(".")[0]
    return re.sub(r"[_-]+", " ", base).strip().title() or domain


def _known_domains(registry: list[ExternalPredictionSource]) -> set[str]:
    out: set[str] = set()
    for src in registry:
        for d in src.domains:
            out.add(d.lower().removeprefix("www."))
    return out


def discover_external_sources(
    *,
    limit: int = 12,
    persist: bool = True,
) -> list[dict[str, Any]]:
    """Scan SearXNG for new NIFTY forecast publishers."""
    registry = load_registry()
    known = _known_domains(registry)
    candidates: list[dict[str, Any]] = []
    seen_domains: set[str] = set()

    for query in _DISCOVERY_QUERIES:
        try:
            results = search_finance(query, limit=10)
        except Exception as exc:
            logger.debug("discovery search failed for %r: %s", query, exc)
            continue
        for row in results:
            url = str(row.get("url") or "")
            domain = _domain(url)
            if not domain or domain in _SKIP_DOMAINS:
                continue
            if domain in known or domain in seen_domains:
                continue
            blob = " ".join(str(row.get(key) or "") for key in ("title", "content")).lower()
            if "nifty" not in blob and "nifty 50" not in blob:
                continue
            seen_domains.add(domain)
            display = _display_name_from_domain(domain)
            snippet = str(row.get("content") or "")[:300]
            candidate = {
                "domain": domain,
                "display_name": display,
                "snippet": snippet,
                "sample_url": url,
                "sample_title": str(row.get("title") or ""),
            }
            candidates.append(candidate)
            if persist:
                merge_discovered_candidate(
                    display_name=display,
                    domain=domain,
                    snippet=snippet,
                )
            if len(candidates) >= limit:
                return candidates[:limit]

    return candidates[:limit]
