"""Load source catalog and resolve chains."""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from trade_integrations.data_router.types import FetchMode, SourceTier


@dataclass(frozen=True)
class SourceSpec:
    id: str
    tier: SourceTier
    markets: frozenset[str]
    domains: frozenset[str]
    adapter: str


_CATALOG_PATH = Path(__file__).with_name("catalog.yaml")


def _env_chain_override(domain: str, market: str) -> list[str] | None:
    key = f"DATA_ROUTER_CHAIN_{domain.upper()}_{market.upper()}"
    raw = os.getenv(key, "").strip()
    if not raw:
        return None
    return [s.strip() for s in raw.split(",") if s.strip()]


@lru_cache(maxsize=1)
def load_catalog() -> dict[str, Any]:
    with _CATALOG_PATH.open(encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


def get_source_spec(source_id: str) -> SourceSpec | None:
    catalog = load_catalog()
    row = (catalog.get("sources") or {}).get(source_id.strip().lower())
    if not row:
        return None
    return SourceSpec(
        id=source_id.strip().lower(),
        tier=row.get("tier", "free"),
        markets=frozenset(row.get("markets") or []),
        domains=frozenset(row.get("domains") or []),
        adapter=str(row.get("adapter") or source_id),
    )


def get_chain(domain: str, market: str) -> list[str]:
    override = _env_chain_override(domain, market)
    if override:
        return override
    catalog = load_catalog()
    chains = catalog.get("chains") or {}
    domain_chains = chains.get(domain.strip().lower()) or {}
    return list(domain_chains.get(market.strip().lower()) or [])


def get_fetch_mode(domain: str) -> FetchMode:
    catalog = load_catalog()
    modes = catalog.get("modes") or {}
    mode = modes.get(domain.strip().lower(), "sequential")
    if mode not in ("sequential", "parallel_merge", "parallel_dedupe"):
        return "sequential"
    return mode  # type: ignore[return-value]


def list_all_sources() -> list[str]:
    catalog = load_catalog()
    return sorted((catalog.get("sources") or {}).keys())
