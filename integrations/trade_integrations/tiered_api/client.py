"""Orchestration: hub-first tiered API fetch with queue + budget."""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Any, Callable

from trade_integrations.tiered_api import budget, hub_store, queue
from trade_integrations.tiered_api.errors import TieredApiBudgetExhausted, TieredApiDisabledError
from trade_integrations.tiered_api.registry import get_spec, is_configured, tiered_api_enabled
from trade_integrations.tiered_api.request_key import TieredRequest, request_hash

logger = logging.getLogger(__name__)


@dataclass
class TieredResult:
    data: Any
    cache_hit: bool
    source: str
    req_hash: str
    budget: dict[str, Any]


def _check_fetch_policy(source: str) -> None:
    try:
        from trade_integrations.dataflows.company_research.fetch_policy import tiered_source_allowed

        if not tiered_source_allowed(source):
            raise TieredApiDisabledError(
                f"Tiered API {source} blocked by fetch policy (e.g. Nifty-50 batch)"
            )
    except ImportError:
        pass


def _force_bypasses_budget() -> bool:
    return os.getenv("TRADE_TIERED_API_FORCE", "").strip().lower() in ("1", "true", "yes")


def _should_persist_raw_cache(source: str, data: Any) -> bool:
    from trade_integrations.tiered_api.cache_policy import should_cache_response

    if os.getenv("TIERED_API_RAW_CACHE", "0").strip().lower() in ("0", "false", "no", "off"):
        return False
    return should_cache_response(data)


def tiered_fetch(
    source: str,
    request: TieredRequest,
    fetch_fn: Callable[[], Any],
    *,
    force: bool = False,
    skip_policy_check: bool = False,
) -> TieredResult:
    """Return hub-cached data or execute fetch_fn through queue + budget."""
    get_spec(source)
    if not tiered_api_enabled():
        data = fetch_fn()
        req_hash = request_hash(source, request)
        return TieredResult(
            data=data,
            cache_hit=False,
            source=source.strip().lower(),
            req_hash=req_hash,
            budget={},
        )

    if not skip_policy_check:
        _check_fetch_policy(source)

    if not is_configured(source):
        from trade_integrations.tiered_api.errors import TieredApiNotConfiguredError

        raise TieredApiNotConfiguredError(f"{source} is not configured")

    req_hash = request_hash(source, request)
    cached = hub_store.load_cached(source, req_hash, force=force)
    if cached is not None:
        return TieredResult(
            data=cached.get("data"),
            cache_hit=True,
            source=source.strip().lower(),
            req_hash=req_hash,
            budget=budget.get_budget_status(source),
        )

    bypass_budget = force and _force_bypasses_budget()
    if not bypass_budget:
        try:
            budget.check_budget_headroom(source)
        except TieredApiBudgetExhausted:
            stale = hub_store.load_cached(source, req_hash, allow_stale=True)
            if stale is not None:
                logger.info(
                    "tiered_api %s: daily budget exhausted; serving stale hub cache",
                    source,
                )
                return TieredResult(
                    data=stale.get("data"),
                    cache_hit=True,
                    source=source.strip().lower(),
                    req_hash=req_hash,
                    budget=budget.get_budget_status(source),
                )
            raise

    queue.acquire_drain_slot(source)
    try:
        if not bypass_budget:
            budget.check_budget_headroom(source)

        data = fetch_fn()
        if not _should_persist_raw_cache(source, data):
            status = budget.record_call(source, req_hash, cache_hit=False)
            return TieredResult(
                data=data,
                cache_hit=False,
                source=source.strip().lower(),
                req_hash=req_hash,
                budget=status,
            )
        hub_store.save_cached(
            source,
            req_hash,
            data,
            request_meta=request.normalized(),
        )
        status = budget.record_call(source, req_hash, cache_hit=False)
        return TieredResult(
            data=data,
            cache_hit=False,
            source=source.strip().lower(),
            req_hash=req_hash,
            budget=status,
        )
    finally:
        queue.release_drain_slot(source)


def get_status_all() -> dict[str, Any]:
    """Aggregate status for CLI / observability."""
    from trade_integrations.tiered_api.registry import list_sources

    sources = []
    for key in list_sources():
        row = budget.get_budget_status(key)
        row["configured"] = is_configured(key)
        row["queue_depth"] = queue.queue_depth(key)
        sources.append(row)
    return {"sources": sources}
