"""Cross-source URL dedup within one external-predictions refresh batch."""

from __future__ import annotations

from urllib.parse import urlparse

from trade_integrations.dataflows.index_research.external_predictions.domain_utils import (
    attribution_score,
    has_stronger_attribution,
)
from trade_integrations.dataflows.index_research.external_predictions.fetcher import (
    SearxngDiscoveryResult,
)
from trade_integrations.dataflows.index_research.external_predictions.models import (
    ExternalPredictionSource,
)

# Syndication-only domain match scores +1; exclusive batch ownership requires stronger signal.
_EXCLUSIVE_OWNERSHIP_MIN_SCORE = 3.0


def normalize_batch_url(url: str) -> str:
    raw = (url or "").strip()
    if not raw:
        return ""
    parsed = urlparse(raw)
    path = (parsed.path or "").rstrip("/").lower()
    host = (parsed.hostname or "").lower().removeprefix("www.")
    return f"{host}{path}"


def _register_owner_candidate(
    best: dict[str, tuple[float, int, str]],
    *,
    key: str,
    source_id: str,
    order: int,
    score: float,
) -> None:
    if score < _EXCLUSIVE_OWNERSHIP_MIN_SCORE:
        return
    prev = best.get(key)
    if prev is None or score > prev[0] or (score == prev[0] and order < prev[1]):
        best[key] = (score, order, source_id)


def assign_discovery_url_owners(
    discovery: dict[str, SearxngDiscoveryResult],
    sources: list[ExternalPredictionSource],
) -> dict[str, str]:
    """Pick one source per normalized URL using attribution score (not watchlist order alone)."""
    source_order = {source.id: idx for idx, source in enumerate(sources)}
    best: dict[str, tuple[float, int, str]] = {}

    for source in sources:
        bundle = discovery.get(source.id) or SearxngDiscoveryResult()
        order = source_order.get(source.id, 999)
        title_map = {
            str(row.get("url") or ""): str(row.get("title") or "")
            for row in bundle.hits
            if row.get("url")
        }
        seen_keys: set[str] = set()
        for row in bundle.hits:
            url = str(row.get("url") or "").strip()
            if not url:
                continue
            key = normalize_batch_url(url)
            if not key:
                continue
            seen_keys.add(key)
            title = str(row.get("title") or "")
            content = str(row.get("content") or "")
            score = attribution_score(source, url, title=title, content=content)
            _register_owner_candidate(
                best,
                key=key,
                source_id=source.id,
                order=order,
                score=score,
            )
        for url in bundle.urls:
            key = normalize_batch_url(str(url or ""))
            if not key or key in seen_keys:
                continue
            title = title_map.get(str(url), "")
            score = attribution_score(source, str(url), title=title)
            _register_owner_candidate(
                best,
                key=key,
                source_id=source.id,
                order=order,
                score=score,
            )

    return {key: source_id for key, (_, _, source_id) in best.items()}


def dedupe_crawl_article_jobs(
    candidates: list[tuple[str, str]],
    sources: list[ExternalPredictionSource],
    attribution_owners: dict[str, str] | None = None,
) -> list[tuple[str, str]]:
    """One crawl job per article URL, respecting discovery attribution when set."""
    if not candidates:
        return []
    source_by_id = {source.id: source for source in sources}
    source_order = {source.id: idx for idx, source in enumerate(sources)}
    owners = dict(attribution_owners or {})
    by_key: dict[str, list[tuple[str, str]]] = {}
    for source_id, url in candidates:
        key = normalize_batch_url(url)
        if not key:
            continue
        by_key.setdefault(key, []).append((source_id, url))

    jobs: list[tuple[str, str]] = []
    for key, entries in by_key.items():
        owner = owners.get(key)
        if owner:
            url = next((u for sid, u in entries if sid == owner), entries[0][1])
            jobs.append((owner, url))
            continue
        best_score = -1.0
        best_order = 999
        best_source = ""
        best_url = entries[0][1]
        for source_id, url in entries:
            source = source_by_id.get(source_id)
            if source is None:
                continue
            score = attribution_score(source, url)
            order = source_order.get(source_id, 999)
            if score < _EXCLUSIVE_OWNERSHIP_MIN_SCORE:
                continue
            if score > best_score or (score == best_score and order < best_order):
                best_score = score
                best_order = order
                best_source = source_id
                best_url = url
        if best_source:
            jobs.append((best_source, best_url))
        else:
            first_source, first_url = entries[0]
            jobs.append((first_source, first_url))
    return jobs


def _row_title(row: object) -> str:
    if isinstance(row, dict):
        return str(row.get("title") or "")
    return str(getattr(row, "title", "") or "")


def filter_hits_for_source(
    hits: list[dict],
    *,
    source: ExternalPredictionSource,
    attribution_owners: dict[str, str],
) -> list[dict]:
    kept: list[dict] = []
    for row in hits:
        url = str(row.get("url") or "").strip()
        if not url:
            continue
        key = normalize_batch_url(url)
        owner = attribution_owners.get(key) if key else None
        title = str(row.get("title") or "")
        if owner and owner != source.id:
            if not has_stronger_attribution(url, source=source, title=title):
                continue
        kept.append(row)
    return kept


class BatchUrlRegistry:
    """Track which source claimed a URL in the current refresh batch."""

    def __init__(self) -> None:
        self._owner: dict[str, str] = {}
        self._attribution_owners: dict[str, str] | None = None

    def set_attribution_owners(self, owners: dict[str, str]) -> None:
        self._attribution_owners = dict(owners)

    @property
    def attribution_owners_initialized(self) -> bool:
        return self._attribution_owners is not None

    @property
    def attribution_owners(self) -> dict[str, str]:
        return dict(self._attribution_owners or {})

    def owner_of(self, url: str) -> str | None:
        key = normalize_batch_url(url)
        return self._owner.get(key) if key else None

    def is_claimed_by_other(self, url: str, source_id: str) -> bool:
        owner = self.owner_of(url)
        return owner is not None and owner != source_id

    def claim(self, url: str, source_id: str) -> None:
        key = normalize_batch_url(url)
        if key:
            self._owner.setdefault(key, source_id)

    def _should_keep_url(
        self,
        url: str,
        *,
        source: ExternalPredictionSource,
        title: str = "",
        attribution_owners: dict[str, str] | None = None,
    ) -> bool:
        owners = attribution_owners if attribution_owners is not None else self._attribution_owners
        key = normalize_batch_url(url)
        attributed_owner = owners.get(key) if key else None
        if attributed_owner and attributed_owner != source.id:
            if not has_stronger_attribution(url, source=source, title=title):
                return False
        if self.is_claimed_by_other(url, source.id):
            if not has_stronger_attribution(url, source=source, title=title):
                return False
        return True

    def filter_urls_for_source(
        self,
        urls: list[str],
        *,
        source: ExternalPredictionSource,
        titles: dict[str, str] | None = None,
        attribution_owners: dict[str, str] | None = None,
    ) -> list[str]:
        titles = titles or {}
        owners = (
            dict(attribution_owners)
            if attribution_owners is not None
            else (self._attribution_owners or {})
        )
        kept: list[str] = []
        for url in urls:
            title = titles.get(url, "")
            if self._should_keep_url(
                url,
                source=source,
                title=title,
                attribution_owners=owners,
            ):
                kept.append(url)
        return kept

    def filter_crawl_rows(
        self,
        rows: list[tuple[str, object]],
        *,
        source: ExternalPredictionSource,
        attribution_owners: dict[str, str] | None = None,
    ) -> list[tuple[str, object]]:
        """Drop crawl rows claimed by another source unless this source has stronger attribution."""
        owners = (
            dict(attribution_owners)
            if attribution_owners is not None
            else (self._attribution_owners or {})
        )
        return [
            (url, row)
            for url, row in rows
            if self._should_keep_url(
                url,
                source=source,
                title=_row_title(row),
                attribution_owners=owners,
            )
        ]


def dedup_discovery_for_batch(
    discovery: dict[str, SearxngDiscoveryResult],
    sources: list[ExternalPredictionSource],
    registry: BatchUrlRegistry,
) -> dict[str, SearxngDiscoveryResult]:
    """Assign syndication URLs to best-attributed source; respect prior batch claims."""
    attribution_owners = assign_discovery_url_owners(discovery, sources)
    registry.set_attribution_owners(attribution_owners)
    out: dict[str, SearxngDiscoveryResult] = {}
    for source in sources:
        bundle = discovery.get(source.id) or SearxngDiscoveryResult()
        title_map = {
            str(row.get("url") or ""): str(row.get("title") or "")
            for row in bundle.hits
            if row.get("url")
        }
        urls = registry.filter_urls_for_source(
            list(bundle.urls),
            source=source,
            titles=title_map,
            attribution_owners=attribution_owners,
        )
        hits = filter_hits_for_source(
            list(bundle.hits),
            source=source,
            attribution_owners=attribution_owners,
        )
        out[source.id] = SearxngDiscoveryResult(
            urls=urls,
            hits=hits,
            queries_run=bundle.queries_run,
            queries_failed=bundle.queries_failed,
            discovery_failed=bundle.discovery_failed,
            domain_filter_exhausted=bundle.domain_filter_exhausted,
        )
    return out
