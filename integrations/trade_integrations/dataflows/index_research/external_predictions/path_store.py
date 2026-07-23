"""Persist navigation traces per (source_id, horizon_days) on the source registry."""

from __future__ import annotations

from trade_integrations.dataflows.index_research.external_predictions.models import (
    ExternalPredictionSource,
    NavigationStep,
    NavigationTrace,
    utc_now_iso,
)
from trade_integrations.dataflows.index_research.external_predictions.source_registry import (
    get_source,
    load_registry,
    save_registry,
)


def _horizon_key(horizon_days: int) -> str:
    return str(max(1, int(horizon_days)))


def get_effective_path(
    source: ExternalPredictionSource,
    *,
    horizon_days: int,
) -> NavigationTrace | None:
    """Prefer user-approved path, then auto-saved path, when not stale."""
    key = _horizon_key(horizon_days)
    for bucket in (source.approved_paths, source.saved_paths):
        trace = bucket.get(key)
        if trace is not None and not trace.stale:
            return trace
    return None


def mark_path_stale(
    source_id: str,
    *,
    horizon_days: int,
) -> None:
    """Mark saved and approved paths stale for this horizon (symmetric with touch_path_success)."""
    registry = load_registry()
    key = _horizon_key(horizon_days)
    changed = False
    for src in registry:
        if src.id != source_id:
            continue
        for bucket in (src.approved_paths, src.saved_paths):
            trace = bucket.get(key)
            if trace is None:
                continue
            trace.stale = True
            trace.replay_failures = int(trace.replay_failures or 0) + 1
            changed = True
        break
    if changed:
        save_registry(registry)


def save_auto_path(
    source_id: str,
    *,
    horizon_days: int,
    final_url: str,
    steps: list[NavigationStep] | None = None,
) -> NavigationTrace | None:
    """Auto-save a successful exploratory navigation path."""
    if not final_url.strip():
        return None
    registry = load_registry()
    key = _horizon_key(horizon_days)
    now = utc_now_iso()
    step_list = list(steps) if steps else [NavigationStep(action="goto", url=final_url)]
    trace = NavigationTrace(
        steps=step_list,
        final_url=final_url,
        approved_by="auto",
        stale=False,
        created_at=now,
        last_success_at=now,
    )
    for src in registry:
        if src.id != source_id:
            continue
        existing = src.saved_paths.get(key)
        if existing is not None and existing.approved_by == "user":
            return existing
        src.saved_paths[key] = trace
        save_registry(registry)
        return trace
    return None


def approve_path(
    source_id: str,
    *,
    horizon_days: int,
) -> NavigationTrace | None:
    """Promote saved path to user-approved."""
    src = get_source(source_id)
    if src is None:
        return None
    key = _horizon_key(horizon_days)
    trace = src.saved_paths.get(key) or src.approved_paths.get(key)
    if trace is None:
        return None
    promoted = NavigationTrace(
        steps=list(trace.steps),
        final_url=trace.final_url,
        approved_by="user",
        stale=False,
        created_at=trace.created_at or utc_now_iso(),
        last_success_at=utc_now_iso(),
        replay_failures=0,
    )
    registry = load_registry()
    for row in registry:
        if row.id != source_id:
            continue
        row.approved_paths[key] = promoted
        row.saved_paths[key] = promoted
        save_registry(registry)
        return promoted
    return None


def touch_path_success(source_id: str, *, horizon_days: int) -> None:
    registry = load_registry()
    key = _horizon_key(horizon_days)
    changed = False
    for src in registry:
        if src.id != source_id:
            continue
        for bucket in (src.approved_paths, src.saved_paths):
            trace = bucket.get(key)
            if trace is None:
                continue
            trace.last_success_at = utc_now_iso()
            trace.stale = False
            trace.replay_failures = 0
            changed = True
        break
    if changed:
        save_registry(registry)
