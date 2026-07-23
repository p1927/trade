"""Validation for user-added external prediction sources."""

from __future__ import annotations

from urllib.parse import urlparse


def _normalize_domain(raw: str) -> str:
    text = str(raw or "").strip().lower().removeprefix("https://").removeprefix("http://")
    if text.startswith("www."):
        text = text[4:]
    return text.split("/")[0].strip()


def _host_matches_domains(host: str, domains: list[str]) -> bool:
    host_norm = host.lower().removeprefix("www.")
    for domain in domains:
        d = _normalize_domain(domain)
        if not d:
            continue
        if host_norm == d or host_norm.endswith(f".{d}"):
            return True
    return False


def validate_user_source_request(
    *,
    display_name: str,
    domains: list[str] | None,
    entry_urls: list[str] | None,
    require_entry_urls: bool = True,
) -> tuple[list[str], list[str], str | None]:
    """
    Validate add-source payload for user watchlist entries.

    Returns ``(normalized_domains, normalized_entry_urls, error_message)``.
    """
    name = str(display_name or "").strip()
    if not name:
        return [], [], "display_name is required"

    domain_list = [_normalize_domain(d) for d in (domains or []) if _normalize_domain(d)]
    domain_list = list(dict.fromkeys(domain_list))
    if not domain_list:
        return [], [], "at least one domain is required"

    url_list: list[str] = []
    for raw in entry_urls or []:
        url = str(raw or "").strip()
        if not url:
            continue
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            return domain_list, [], f"invalid entry URL: {url[:120]}"
        host = (parsed.hostname or "").lower()
        if not _host_matches_domains(host, domain_list):
            return domain_list, [], f"entry URL host must match configured domain(s): {url[:120]}"
        url_list.append(url)
    url_list = list(dict.fromkeys(url_list))

    if require_entry_urls and not url_list:
        return domain_list, [], "at least one entry_url is required for user-added sources"

    return domain_list, url_list, None
