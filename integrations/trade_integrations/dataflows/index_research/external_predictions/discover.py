"""Discover new external prediction source candidates via SearXNG."""

from __future__ import annotations

import logging
import re
from typing import Any

from trade_integrations.dataflows.index_research.external_predictions.domain_utils import (
    discovery_allowed_domains,
    host_from_url,
    infer_discovered_kind,
    is_discovery_redundant_domain,
    normalize_domain,
)
from trade_integrations.dataflows.index_research.external_predictions.models import (
    ExternalPredictionSource,
    SourceKind,
)
from trade_integrations.dataflows.index_research.external_predictions.source_registry import (
    load_registry,
    merge_discovered_candidate,
)
from trade_integrations.dataflows.searxng_finance import TRUSTED_FINANCE_DOMAINS, search_finance

logger = logging.getLogger(__name__)

_DISCOVERY_QUERIES = (
    "Nifty 50 forecast outlook target analyst",
    "Nifty 50 target price brokerage report India",
    "Nifty 50 year end target global bank India",
    "Nifty 50 research report Indian brokerage target",
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


def _display_name_from_domain(domain: str, *, kind: SourceKind, title: str = "") -> str:
    if title.strip():
        cleaned = re.sub(r"\s*[-|]\s*(economic times|moneycontrol|livemint).*$", "", title, flags=re.I)
        cleaned = re.sub(r"\s+", " ", cleaned).strip()
        if 4 <= len(cleaned) <= 80:
            return cleaned
    base = domain.split(".")[0]
    name = re.sub(r"[_-]+", " ", base).strip().title() or domain
    if kind == "broker" and "Securities" not in name and name.endswith("Sec"):
        name = f"{name[:-3]} Securities"
    return name


def _known_domains(registry: list[ExternalPredictionSource]) -> set[str]:
    out: set[str] = set()
    for src in registry:
        for d in src.domains:
            out.add(normalize_domain(d))
    return out


def discover_external_sources(
    *,
    limit: int = 12,
    persist: bool = True,
) -> list[dict[str, Any]]:
    """Scan SearXNG for new NIFTY forecast publishers (media + broker-native)."""
    registry = load_registry()
    known = _known_domains(registry)
    allowed = discovery_allowed_domains(registry, trusted_domains=TRUSTED_FINANCE_DOMAINS)
    candidates: list[dict[str, Any]] = []
    seen_domains: set[str] = set()

    for query in _DISCOVERY_QUERIES:
        try:
            results = search_finance(query, limit=10, allowed_domains=allowed)
        except Exception as exc:
            logger.debug("discovery search failed for %r: %s", query, exc)
            continue
        for row in results:
            url = str(row.get("url") or "")
            domain = host_from_url(url)
            if not domain or domain in _SKIP_DOMAINS:
                continue
            if domain in known or domain in seen_domains:
                continue
            if is_discovery_redundant_domain(domain, registry):
                continue
            title = str(row.get("title") or "")
            snippet = str(row.get("content") or "")[:300]
            blob = f"{title} {snippet}".lower()
            if "nifty" not in blob and "nifty 50" not in blob and "nifty50" not in blob:
                continue
            kind = infer_discovered_kind(domain, title=title, snippet=snippet)
            seen_domains.add(domain)
            display = _display_name_from_domain(domain, kind=kind, title=title)
            candidate = {
                "domain": domain,
                "display_name": display,
                "kind": kind,
                "snippet": snippet,
                "sample_url": url,
                "sample_title": title,
            }
            candidates.append(candidate)
            if persist:
                merge_discovered_candidate(
                    display_name=display,
                    domain=domain,
                    snippet=snippet,
                    kind=kind,
                )
            if len(candidates) >= limit:
                return candidates[:limit]

    return candidates[:limit]
