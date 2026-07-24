"""Domain classification and URL attribution for external prediction sources."""

from __future__ import annotations

import re
from urllib.parse import urlparse

from trade_integrations.dataflows.index_research.external_predictions.models import (
    ExternalPredictionSource,
    SourceKind,
)

SYNDICATION_DOMAINS = frozenset(
    {
        "economictimes.indiatimes.com",
        "economictimes.com",
        "moneycontrol.com",
        "livemint.com",
    }
)

_GLOBAL_BANK_TOPIC_SLUGS: dict[str, tuple[str, ...]] = {
    "goldman_sachs": ("goldman-sachs", "goldman-sachs-nifty"),
    "morgan_stanley": ("morgan-stanley", "morgan-stanley-nifty"),
}

_GLOBAL_BANK_NAME_ALIASES: dict[str, tuple[str, ...]] = {
    "goldman_sachs": ("goldman sachs", "goldman"),
    "morgan_stanley": ("morgan stanley",),
}

_BROKER_DOMAIN_MARKERS = (
    "sec.com",
    "securities",
    "direct.com",
    "broking",
    "oswal",
    "angelone",
    "sharekhan",
    "kotak",
    "iifl",
    "5paisa",
)


def normalize_domain(raw: str) -> str:
    text = str(raw or "").strip().lower().removeprefix("https://").removeprefix("http://")
    if text.startswith("www."):
        text = text[4:]
    return text.split("/")[0].strip()


def host_from_url(url: str) -> str:
    return (urlparse(str(url or "")).hostname or "").lower().removeprefix("www.")


def is_syndication_domain(domain: str) -> bool:
    d = normalize_domain(domain)
    return bool(d) and d in SYNDICATION_DOMAINS


def native_domains(source: ExternalPredictionSource) -> tuple[str, ...]:
    """Native publisher hosts for this source.

    Media sources treat all configured domains as native. Brokers exclude
    syndication fallbacks (ET, Moneycontrol, Livemint) from native attribution.
    Global banks have no owned crawl hosts — syndication is searched but not native.
    """
    if source.kind == "global_bank":
        return ()
    out: list[str] = []
    for domain in source.domains or []:
        norm = normalize_domain(domain)
        if not norm:
            continue
        if source.kind == "media" or not is_syndication_domain(norm):
            out.append(norm)
    return tuple(dict.fromkeys(out))


def attribution_name_tokens(source: ExternalPredictionSource) -> tuple[str, ...]:
    """Lower-case tokens used to match publisher name in titles/snippets."""
    tokens: list[str] = []
    display = source.display_name.strip().lower()
    if display:
        tokens.append(display)
    for alias in _GLOBAL_BANK_NAME_ALIASES.get(source.id, ()):
        if alias:
            tokens.append(alias.lower())
    return tuple(dict.fromkeys(t for t in tokens if t))


def url_matches_bank_topic(url: str, source: ExternalPredictionSource) -> bool:
    """True when a global-bank source owns a syndication topic/listing URL."""
    if source.kind != "global_bank":
        return False
    slugs = _GLOBAL_BANK_TOPIC_SLUGS.get(source.id, ())
    if not slugs:
        return False
    path = (urlparse(str(url or "")).path or "").lower()
    return any(f"/topic/{slug}" in path or f"/tags/{slug}" in path for slug in slugs)


def discovery_allowed_domains(
    registry: list[ExternalPredictionSource],
    *,
    trusted_domains: tuple[str, ...],
) -> tuple[str, ...]:
    """Union trusted finance portals with broker-native hosts from the registry."""
    merged: list[str] = list(trusted_domains)
    seen = {normalize_domain(d) for d in merged if normalize_domain(d)}
    for src in registry:
        for domain in native_domains(src):
            norm = normalize_domain(domain)
            if norm and norm not in seen:
                seen.add(norm)
                merged.append(norm)
    return tuple(merged)


def is_discovery_redundant_domain(
    domain: str,
    registry: list[ExternalPredictionSource],
) -> bool:
    """Skip syndication hosts already registered as broker/bank fallbacks."""
    norm = normalize_domain(domain)
    if not norm or not is_syndication_domain(norm):
        return False
    for src in registry:
        for configured in src.domains or []:
            if normalize_domain(configured) == norm:
                return True
    return False


def infer_discovered_kind(domain: str, *, title: str = "", snippet: str = "") -> SourceKind:
    """Heuristic kind for auto-discovered publishers."""
    blob = f"{title} {snippet} {domain}".lower()
    d = normalize_domain(domain)
    if any(marker in d for marker in _BROKER_DOMAIN_MARKERS):
        return "broker"
    if re.search(r"\b(goldman|morgan stanley|jp morgan|citi|barclays|ubs)\b", blob):
        return "global_bank"
    if "brokerage" in blob or " broking " in f" {blob} ":
        return "broker"
    return "media"


def primary_domain(source: ExternalPredictionSource) -> str:
    native = native_domains(source)
    if native:
        return native[0]
    if source.domains:
        return normalize_domain(source.domains[0])
    return ""


def url_host_matches_domain(url: str, domain: str) -> bool:
    host = host_from_url(url)
    d = normalize_domain(domain)
    if not host or not d:
        return False
    return host == d or host.endswith(f".{d}")


def url_matches_native_domain(url: str, source: ExternalPredictionSource) -> bool:
    return any(url_host_matches_domain(url, d) for d in native_domains(source))


def url_matches_any_source_domain(url: str, source: ExternalPredictionSource) -> bool:
    return any(
        url_host_matches_domain(url, normalize_domain(d))
        for d in (source.domains or [])
        if normalize_domain(d)
    )


def _name_in_blob(blob: str, source: ExternalPredictionSource) -> bool:
    return any(token in blob for token in attribution_name_tokens(source))


def attribution_score(
    source: ExternalPredictionSource,
    url: str,
    *,
    title: str = "",
    content: str = "",
) -> float:
    """Higher score = stronger claim that this source owns the forecast at ``url``."""
    blob = f"{title} {content} {url}".lower()
    score = 0.0
    if _name_in_blob(blob, source):
        score += 10.0
    if url_matches_native_domain(url, source):
        score += 6.0
    elif url_matches_bank_topic(url, source):
        score += 7.0
    elif url_matches_any_source_domain(url, source):
        score += 1.0
    return score


def has_stronger_attribution(
    url: str,
    *,
    source: ExternalPredictionSource,
    title: str = "",
    content: str = "",
) -> bool:
    """True when this source clearly owns the article (name, native, or bank topic)."""
    blob = f"{title} {content} {url}".lower()
    if _name_in_blob(blob, source):
        return True
    if url_matches_native_domain(url, source):
        return True
    return url_matches_bank_topic(url, source)
