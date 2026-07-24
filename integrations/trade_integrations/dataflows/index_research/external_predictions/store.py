"""Hub JSON persistence for external prediction snapshots."""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from trade_integrations.dataflows.index_research.external_predictions.models import (
    ExternalPredictionRecord,
    ExternalPredictionSnapshot,
    utc_now_iso,
)
from trade_integrations.dataflows.index_research.external_predictions.source_registry import (
    external_predictions_root,
)

_DEFAULT_SYMBOL = "NIFTY"
_DEFAULT_TTL_HOURS = 24


def snapshot_path(symbol: str, horizon_days: int) -> Path:
    return external_predictions_root(symbol) / f"latest_{horizon_days}d.json"


def source_latest_path(symbol: str, source_id: str, horizon_days: int | None = None) -> Path:
    base = external_predictions_root(symbol) / "sources" / source_id
    if horizon_days is not None:
        return base / f"latest_{int(horizon_days)}d.json"
    return base / "latest.json"


def source_history_path(symbol: str, source_id: str, iso_day: str) -> Path:
    return external_predictions_root(symbol) / "sources" / source_id / "history" / f"{iso_day}.json"


def cache_ttl_hours() -> int:
    try:
        return int(os.getenv("EXTERNAL_PREDICTIONS_CACHE_TTL_HOURS", str(_DEFAULT_TTL_HOURS)))
    except ValueError:
        return _DEFAULT_TTL_HOURS


def rollup_refresh_status(predictions: list[ExternalPredictionRecord]) -> dict[str, int | bool]:
    """Aggregate per-source fetch_status counts for batch refresh rollups."""
    ok = sum(1 for p in predictions if p.fetch_status == "ok")
    err = sum(1 for p in predictions if p.fetch_status == "error")
    not_found = sum(1 for p in predictions if p.fetch_status == "not_found")
    return {
        "sources_ok": ok,
        "sources_error": err,
        "sources_not_found": not_found,
        "had_errors": err > 0 or not_found > 0,
    }


def _parse_iso(ts: str) -> datetime | None:
    if not ts:
        return None
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except ValueError:
        return None


def is_snapshot_stale(fetched_at: str, *, ttl_hours: int | None = None) -> bool:
    ttl = ttl_hours if ttl_hours is not None else cache_ttl_hours()
    dt = _parse_iso(fetched_at)
    if dt is None:
        return True
    return datetime.now(timezone.utc) - dt > timedelta(hours=ttl)


def upsert_prediction(
    record: ExternalPredictionRecord,
    *,
    symbol: str = _DEFAULT_SYMBOL,
    write_history: bool = True,
) -> None:
    sym = symbol.upper()
    hz = int(record.horizon_days or 14)
    path = source_latest_path(sym, record.source_id, hz)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(record.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8")
    if write_history and record.as_of:
        day = record.as_of[:10]
        hist = source_history_path(sym, record.source_id, day)
        hist.parent.mkdir(parents=True, exist_ok=True)
        hist.write_text(json.dumps(record.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8")


def _attempt_history_path(symbol: str, source_id: str, iso_day: str) -> Path:
    return source_history_path(symbol, source_id, iso_day).with_name(f"{iso_day}-attempts.jsonl")


def append_refresh_attempt_history(
    record: ExternalPredictionRecord,
    *,
    symbol: str = _DEFAULT_SYMBOL,
) -> None:
    """Append a failed refresh attempt for audit (does not change latest.json)."""
    sym = symbol.upper()
    day = (record.as_of or utc_now_iso())[:10]
    path = _attempt_history_path(sym, record.source_id, day)
    path.parent.mkdir(parents=True, exist_ok=True)
    row = {
        "at": utc_now_iso(),
        "fetch_status": record.fetch_status,
        "error_message": record.error_message or "",
        "provenance": dict(record.provenance or {}),
    }
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def persist_refresh_result(
    record: ExternalPredictionRecord,
    *,
    symbol: str = _DEFAULT_SYMBOL,
    write_history: bool = True,
) -> tuple[ExternalPredictionRecord, ExternalPredictionRecord]:
    """
    Persist a refresh attempt.

    Failed attempts never replace a prior ok forecast in latest.json. The prior ok
    forecast is retained and annotated with ``provenance.last_refresh_attempt``.

    Returns ``(stored_record, attempt_record)`` — stored is what snapshot readers
    load from disk; attempt is the outcome of this refresh (for pipeline logs).
    """
    sym = symbol.upper()
    attempt = record
    existing = load_source_prediction(
        attempt.source_id,
        symbol=sym,
        horizon_days=attempt.horizon_days,
    )

    if attempt.fetch_status == "ok":
        upsert_prediction(attempt, symbol=sym, write_history=write_history)
        return attempt, attempt

    if write_history:
        append_refresh_attempt_history(attempt, symbol=sym)

    if existing is not None and existing.fetch_status == "ok":
        prov = dict(existing.provenance or {})
        attempt_prov = dict(attempt.provenance or {})
        prov["last_refresh_attempt"] = {
            "at": utc_now_iso(),
            "fetch_status": attempt.fetch_status,
            "error_message": attempt.error_message or "",
            "searxng_trigger": attempt_prov.get("searxng_trigger"),
            "searxng_attempted": attempt_prov.get("searxng_attempted"),
            "urls_tried": attempt_prov.get("urls_tried"),
        }
        existing.provenance = prov
        upsert_prediction(existing, symbol=sym, write_history=False)
        return existing, attempt

    upsert_prediction(attempt, symbol=sym, write_history=write_history)
    return attempt, attempt


def load_source_prediction(
    source_id: str,
    *,
    symbol: str = _DEFAULT_SYMBOL,
    horizon_days: int | None = None,
) -> ExternalPredictionRecord | None:
    sym = symbol.upper()
    candidates: list[Path] = []
    if horizon_days is not None:
        candidates.append(source_latest_path(sym, source_id, horizon_days))
    legacy = source_latest_path(sym, source_id)
    candidates.append(legacy)
    source_dir = legacy.parent
    if source_dir.is_dir():
        for path in sorted(source_dir.glob("latest_*.json"), reverse=True):
            if path not in candidates:
                candidates.append(path)
    seen: set[Path] = set()
    for path in candidates:
        if path in seen or not path.is_file():
            continue
        seen.add(path)
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        record = ExternalPredictionRecord.from_dict(data)
        if record is None:
            continue
        if horizon_days is not None and record.horizon_days != horizon_days:
            continue
        return record
    return None


def rebuild_snapshot(
    *,
    symbol: str = _DEFAULT_SYMBOL,
    horizon_days: int,
    internal_forecast: dict[str, Any] | None = None,
    fetched_at: str | None = None,
    refresh_completed_at: str | None = None,
    refresh_attempt_failures: int = 0,
) -> ExternalPredictionSnapshot:
    from trade_integrations.dataflows.index_research.external_predictions.source_registry import (
        load_registry,
        watchlisted_sources,
    )

    sym = symbol.upper()
    sources = watchlisted_sources()
    predictions: list[ExternalPredictionRecord] = []
    for src in sources:
        rec = load_source_prediction(src.id, symbol=sym, horizon_days=horizon_days)
        if rec is not None:
            predictions.append(rec)
        else:
            predictions.append(
                ExternalPredictionRecord(
                    source_id=src.id,
                    symbol=sym,
                    horizon_days=horizon_days,
                    fetch_status="not_found",
                )
            )
    ts = utc_now_iso() if fetched_at is None else fetched_at
    ttl = cache_ttl_hours()
    rollup = rollup_refresh_status(predictions)
    completed_at = str(refresh_completed_at or "").strip()
    snapshot = ExternalPredictionSnapshot(
        symbol=sym,
        horizon_days=horizon_days,
        fetched_at=ts,
        refresh_completed_at=completed_at,
        cache_ttl_hours=ttl,
        is_stale=is_snapshot_stale(ts, ttl_hours=ttl),
        sources=sources,
        predictions=predictions,
        internal_forecast=internal_forecast,
        sources_ok=int(rollup["sources_ok"]),
        sources_error=int(rollup["sources_error"]),
        sources_not_found=int(rollup["sources_not_found"]),
        had_errors=bool(rollup["had_errors"]),
        refresh_attempt_failures=max(0, int(refresh_attempt_failures)),
    )
    save_snapshot(snapshot)
    return snapshot


def save_snapshot(snapshot: ExternalPredictionSnapshot) -> None:
    path = snapshot_path(snapshot.symbol, snapshot.horizon_days)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(snapshot.to_dict(), indent=2, ensure_ascii=False, default=str),
        encoding="utf-8",
    )


def load_snapshot(
    *,
    symbol: str = _DEFAULT_SYMBOL,
    horizon_days: int,
) -> ExternalPredictionSnapshot:
    from trade_integrations.dataflows.index_research.external_predictions.source_registry import (
        load_registry,
        watchlisted_sources,
    )

    sym = symbol.upper()
    load_registry()
    path = snapshot_path(sym, horizon_days)
    if not path.is_file():
        return rebuild_snapshot(symbol=sym, horizon_days=horizon_days, fetched_at="")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return rebuild_snapshot(symbol=sym, horizon_days=horizon_days, fetched_at="")
    snapshot = ExternalPredictionSnapshot.from_dict(data)
    if snapshot.symbol != sym:
        snapshot.symbol = sym
    if snapshot.horizon_days != horizon_days:
        snapshot.horizon_days = horizon_days
    ttl = cache_ttl_hours()
    snapshot.cache_ttl_hours = ttl
    snapshot.is_stale = is_snapshot_stale(snapshot.fetched_at, ttl_hours=ttl)
    if not snapshot.sources:
        snapshot.sources = watchlisted_sources()
    return snapshot
