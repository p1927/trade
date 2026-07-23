"""Seed and user-managed source registry for external predictions."""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from trade_integrations.dataflows.index_research.external_predictions.curated_urls import (
    curated_urls_for_source,
)
from trade_integrations.dataflows.index_research.external_predictions.models import (
    ExternalPredictionSource,
    utc_now_iso,
)
from trade_integrations.context.hub import get_hub_dir

_DEFAULT_SYMBOL = "NIFTY"


def external_predictions_root(symbol: str = _DEFAULT_SYMBOL) -> Path:
    return get_hub_dir() / symbol.upper() / "external_predictions"


def registry_path(symbol: str = _DEFAULT_SYMBOL) -> Path:
    return external_predictions_root(symbol) / "source_registry.json"

_SEED_KEYWORDS: tuple[str, ...] = (
    "forecast",
    "target",
    "prediction",
    "outlook",
    "analyst",
    "nifty",
    "nifty 50",
)


def _seed_source(
    *,
    id: str,
    display_name: str,
    kind: str,
    domains: list[str],
    search_queries: list[str],
) -> ExternalPredictionSource:
    curated = curated_urls_for_source(id)
    return ExternalPredictionSource(
        id=id,
        display_name=display_name,
        kind=kind,  # type: ignore[arg-type]
        search_queries=search_queries,
        domains=domains,
        landing_urls=list(curated),
        curated_urls=list(curated),
        search_keywords=list(_SEED_KEYWORDS),
        watchlisted=True,
        added_by="seed",
        removable=False,
    )


_BROKER_LANDING_URLS: dict[str, tuple[str, ...]] = {
    "motilal_oswal": ("https://www.motilaloswal.com/research-and-reports",),
    "icici_direct": ("https://www.icicidirect.com/research/equity",),
    "hdfc_securities": ("https://www.hdfcsec.com/research-and-reports",),
}

_REGISTRY_OVERRIDES: dict[str, dict[str, Any]] = {
    "choice_india": {
        "landing_urls": ["https://choiceindia.com/blog"],
        "entry_urls": ["https://choiceindia.com/blog"],
        "curated_urls": [
            "https://choiceindia.com/blog/indian-stock-market-prediction-for-next-week",
        ],
    },
    "livemint": {
        "landing_urls": ["https://www.livemint.com/market/stock-market-news"],
        "curated_urls": [
            "https://www.livemint.com/market/stock-market-news",
            "https://www.livemint.com/market",
        ],
    },
}


_SEED_SOURCES: tuple[ExternalPredictionSource, ...] = (
    _seed_source(
        id="moneycontrol",
        display_name="Moneycontrol",
        kind="media",
        domains=["moneycontrol.com"],
        search_queries=[
            "Nifty 50 target {horizon} days",
            "Nifty 50 forecast outlook {year}",
        ],
    ),
    _seed_source(
        id="economictimes",
        display_name="Economic Times",
        kind="media",
        domains=["economictimes.indiatimes.com", "economictimes.com"],
        search_queries=[
            "Nifty 50 target {horizon} days site:economictimes.indiatimes.com",
            "Nifty 50 outlook forecast {year}",
        ],
    ),
    _seed_source(
        id="livemint",
        display_name="Livemint",
        kind="media",
        domains=["livemint.com"],
        search_queries=[
            "Nifty 50 target forecast {horizon} days site:livemint.com",
            "Nifty 50 outlook {year}",
        ],
    ),
    ExternalPredictionSource(
        id="motilal_oswal",
        display_name="Motilal Oswal",
        kind="broker",
        search_queries=[
            "Motilal Oswal Nifty 50 target {horizon} days",
            "Motilal Oswal Nifty outlook {year}",
        ],
        domains=["motilaloswal.com", "economictimes.indiatimes.com", "moneycontrol.com"],
        landing_urls=list(_BROKER_LANDING_URLS["motilal_oswal"]),
        curated_urls=list(curated_urls_for_source("motilal_oswal")),
        search_keywords=list(_SEED_KEYWORDS),
        watchlisted=True,
        added_by="seed",
        removable=False,
    ),
    ExternalPredictionSource(
        id="icici_direct",
        display_name="ICICI Direct",
        kind="broker",
        search_queries=[
            "ICICI Direct Nifty 50 target {horizon} days",
            "ICICI Direct Nifty outlook forecast",
        ],
        domains=["icicidirect.com", "economictimes.indiatimes.com", "moneycontrol.com"],
        landing_urls=list(_BROKER_LANDING_URLS["icici_direct"]),
        curated_urls=list(curated_urls_for_source("icici_direct")),
        search_keywords=list(_SEED_KEYWORDS),
        watchlisted=True,
        added_by="seed",
        removable=False,
    ),
    ExternalPredictionSource(
        id="hdfc_securities",
        display_name="HDFC Securities",
        kind="broker",
        search_queries=[
            "HDFC Securities Nifty 50 target {horizon} days",
            "HDFC Securities Nifty outlook {year}",
        ],
        domains=["hdfcsec.com", "economictimes.indiatimes.com", "moneycontrol.com"],
        landing_urls=list(_BROKER_LANDING_URLS["hdfc_securities"]),
        curated_urls=list(curated_urls_for_source("hdfc_securities")),
        search_keywords=list(_SEED_KEYWORDS),
        watchlisted=True,
        added_by="seed",
        removable=False,
    ),
    _seed_source(
        id="goldman_sachs",
        display_name="Goldman Sachs",
        kind="global_bank",
        domains=["economictimes.indiatimes.com", "livemint.com", "moneycontrol.com"],
        search_queries=[
            "Goldman Sachs India Nifty 50 target {year}",
            "Goldman Sachs Nifty forecast India equity outlook",
        ],
    ),
    _seed_source(
        id="morgan_stanley",
        display_name="Morgan Stanley",
        kind="global_bank",
        domains=["economictimes.indiatimes.com", "livemint.com", "moneycontrol.com"],
        search_queries=[
            "Morgan Stanley India Nifty 50 target {year}",
            "Morgan Stanley Nifty forecast India outlook",
        ],
    ),
)


def _slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", value.strip().lower())
    return slug.strip("_") or "source"


def _seed_by_id() -> dict[str, ExternalPredictionSource]:
    return {src.id: src for src in _SEED_SOURCES}


def resync_curated_urls(*, persist: bool = True) -> list[ExternalPredictionSource]:
    """Align persisted registry curated/landing URLs with curated_urls.py defaults."""
    path = registry_path()
    if not path.is_file():
        return seed_registry_if_missing()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return seed_registry_if_missing()
    rows = payload.get("sources") if isinstance(payload, dict) else payload
    if not isinstance(rows, list):
        return seed_registry_if_missing()
    sources: list[ExternalPredictionSource] = []
    for row in rows:
        src = ExternalPredictionSource.from_dict(row)
        if src is not None:
            sources.append(src)
    changed = False
    for src in sources:
        defaults = list(curated_urls_for_source(src.id))
        if defaults and list(src.curated_urls or []) != defaults:
            src.curated_urls = defaults
            changed = True
        broker_landing = _BROKER_LANDING_URLS.get(src.id)
        if broker_landing and list(src.landing_urls or []) != list(broker_landing):
            src.landing_urls = list(broker_landing)
            changed = True
        override = _REGISTRY_OVERRIDES.get(src.id)
        if override:
            for key, value in override.items():
                if list(getattr(src, key, None) or []) != list(value):
                    setattr(src, key, list(value))
                    changed = True
    if changed and persist:
        save_registry(sources)
    return sources


def _merge_seed_defaults(sources: list[ExternalPredictionSource]) -> list[ExternalPredictionSource]:
    """Backfill landing/curated URLs and keywords from seed registry for known seed sources."""
    seed_map = _seed_by_id()
    changed = False
    out: list[ExternalPredictionSource] = []
    for src in sources:
        seed = seed_map.get(src.id)
        if seed is None or src.added_by != "seed":
            out.append(src)
            continue
        if not src.curated_urls and seed.curated_urls:
            src.curated_urls = list(seed.curated_urls)
            changed = True
        if not src.landing_urls and seed.landing_urls:
            src.landing_urls = list(seed.landing_urls)
            changed = True
        if not src.search_keywords and seed.search_keywords:
            src.search_keywords = list(seed.search_keywords)
            changed = True
        if src.domains != seed.domains:
            src.domains = list(seed.domains)
            changed = True
        out.append(src)
    if changed:
        save_registry(out)
    return out


def _read_registry_file(path: Path) -> list[ExternalPredictionSource]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return list(_SEED_SOURCES)
    rows = payload.get("sources") if isinstance(payload, dict) else payload
    if not isinstance(rows, list):
        return list(_SEED_SOURCES)
    out: list[ExternalPredictionSource] = []
    for row in rows:
        src = ExternalPredictionSource.from_dict(row)
        if src is not None:
            out.append(src)
    return _merge_seed_defaults(out or list(_SEED_SOURCES))


def seed_registry_if_missing() -> list[ExternalPredictionSource]:
    path = registry_path()
    if path.is_file():
        return _read_registry_file(path)
    root = external_predictions_root()
    root.mkdir(parents=True, exist_ok=True)
    sources = list(_SEED_SOURCES)
    save_registry(sources)
    return sources


def load_registry() -> list[ExternalPredictionSource]:
    path = registry_path()
    if not path.is_file():
        return seed_registry_if_missing()
    return _read_registry_file(path)


def save_registry(sources: list[ExternalPredictionSource]) -> None:
    path = registry_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "updated_at": utc_now_iso(),
        "sources": [s.to_dict() for s in sources],
    }
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def watchlisted_sources() -> list[ExternalPredictionSource]:
    return [s for s in load_registry() if s.watchlisted]


def get_source(source_id: str) -> ExternalPredictionSource | None:
    key = source_id.strip().lower()
    for src in load_registry():
        if src.id == key:
            return src
    return None


def add_source_to_watchlist(
    *,
    source_id: str | None = None,
    display_name: str,
    domains: list[str] | None = None,
    search_queries: list[str] | None = None,
    entry_urls: list[str] | None = None,
    kind: str = "media",
    added_by: str = "user",
) -> ExternalPredictionSource:
    registry = load_registry()
    sid = _slugify(source_id or display_name)
    existing = next((s for s in registry if s.id == sid), None)
    if existing is not None:
        existing.watchlisted = True
        if domains:
            existing.domains = list(dict.fromkeys(existing.domains + domains))
        if entry_urls:
            existing.entry_urls = list(dict.fromkeys(list(existing.entry_urls or []) + entry_urls))
        if search_queries:
            merged = list(dict.fromkeys(existing.search_queries + search_queries))
            existing.search_queries = merged
        save_registry(registry)
        return existing

    src_kind = kind if kind in {"media", "broker", "global_bank"} else "media"
    src_added = added_by if added_by in {"seed", "user", "discover"} else "user"
    new_source = ExternalPredictionSource(
        id=sid,
        display_name=display_name.strip() or sid,
        kind=src_kind,  # type: ignore[arg-type]
        search_queries=list(search_queries or [
            f"{display_name} Nifty 50 target {{horizon}} days",
            f"{display_name} Nifty 50 forecast {{year}}",
        ]),
        domains=list(domains or []),
        entry_urls=list(entry_urls or []),
        watchlisted=True,
        discovered_at=utc_now_iso() if src_added == "discover" else None,
        added_by=src_added,  # type: ignore[arg-type]
        removable=True,
    )
    registry.append(new_source)
    save_registry(registry)
    return new_source


def remove_source_from_watchlist(source_id: str) -> bool:
    registry = load_registry()
    key = source_id.strip().lower()
    changed = False
    for src in registry:
        if src.id != key:
            continue
        if not src.removable:
            return False
        src.watchlisted = False
        changed = True
        break
    if changed:
        save_registry(registry)
    return changed


def merge_discovered_candidate(
    *,
    display_name: str,
    domain: str,
    snippet: str = "",
) -> ExternalPredictionSource | None:
    """Register a discovery candidate without watchlisting."""
    registry = load_registry()
    domain = domain.strip().lower().removeprefix("www.")
    if any(domain in d or d in domain for src in registry for d in src.domains):
        return None
    sid = _slugify(display_name or domain.split(".")[0])
    if any(s.id == sid for s in registry):
        return None
    candidate = ExternalPredictionSource(
        id=sid,
        display_name=display_name.strip() or domain,
        kind="media",
        search_queries=[
            f"{display_name or domain} Nifty 50 target {{horizon}} days",
            f"Nifty 50 forecast {display_name or domain} {{year}}",
        ],
        domains=[domain],
        watchlisted=False,
        discovered_at=utc_now_iso(),
        added_by="discover",
        removable=True,
    )
    registry.append(candidate)
    save_registry(registry)
    return candidate


def format_queries(source: ExternalPredictionSource, *, horizon_days: int) -> list[str]:
    year = str(datetime.now(timezone.utc).year)
    out: list[str] = []
    for template in source.search_queries or [f"{source.display_name} Nifty 50 target {{horizon}} days"]:
        q = (
            template.replace("{horizon}", str(horizon_days))
            .replace("{year}", year)
        )
        out.append(q)
    return out
