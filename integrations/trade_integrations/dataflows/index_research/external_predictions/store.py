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


def source_latest_path(symbol: str, source_id: str) -> Path:
    return external_predictions_root(symbol) / "sources" / source_id / "latest.json"


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
    path = source_latest_path(sym, record.source_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(record.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8")
    if write_history and record.as_of:
        day = record.as_of[:10]
        hist = source_history_path(sym, record.source_id, day)
        hist.parent.mkdir(parents=True, exist_ok=True)
        hist.write_text(json.dumps(record.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8")


def load_source_prediction(
    source_id: str,
    *,
    symbol: str = _DEFAULT_SYMBOL,
    horizon_days: int | None = None,
) -> ExternalPredictionRecord | None:
    path = source_latest_path(symbol.upper(), source_id)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    record = ExternalPredictionRecord.from_dict(data)
    if record is None:
        return None
    if horizon_days is not None and record.horizon_days != horizon_days:
        return None
    return record


def rebuild_snapshot(
    *,
    symbol: str = _DEFAULT_SYMBOL,
    horizon_days: int,
    internal_forecast: dict[str, Any] | None = None,
    fetched_at: str | None = None,
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
    snapshot = ExternalPredictionSnapshot(
        symbol=sym,
        horizon_days=horizon_days,
        fetched_at=ts,
        cache_ttl_hours=ttl,
        is_stale=is_snapshot_stale(ts, ttl_hours=ttl),
        sources=sources,
        predictions=predictions,
        internal_forecast=internal_forecast,
        sources_ok=int(rollup["sources_ok"]),
        sources_error=int(rollup["sources_error"]),
        sources_not_found=int(rollup["sources_not_found"]),
        had_errors=bool(rollup["had_errors"]),
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
