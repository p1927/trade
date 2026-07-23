"""Idempotent migrations from legacy verified news records to distilled events SSOT."""

from __future__ import annotations

import json
import logging
import os
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from trade_integrations.context.hub import get_hub_dir
from trade_integrations.hub_storage.news_events_store import (
    count_events,
    ensure_events_storage,
    event_from_verified_record,
    existing_event_ids,
    upsert_event,
)
from trade_integrations.hub_storage.verified_news_store import (
    _RECORD_COLUMNS,
    count_verified_records,
    iter_legacy_verified_records,
    list_verified_tickers,
    verified_records_path,
)
from trade_integrations.hub_storage.parquet_io import write_dataframe

logger = logging.getLogger(__name__)

MIGRATION_SCHEMA_VERSION = 2
_MIGRATION_STATE_REL = Path("_data") / "news_events" / "migration_state.json"
SSOT_MODE = "events"


def migration_state_path() -> Path:
    return get_hub_dir() / _MIGRATION_STATE_REL


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_migration_state() -> dict[str, Any]:
    path = migration_state_path()
    if not path.is_file():
        return {"schema_version": MIGRATION_SCHEMA_VERSION, "ssot": SSOT_MODE, "tickers": {}}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return {"schema_version": MIGRATION_SCHEMA_VERSION, "ssot": SSOT_MODE, "tickers": {}}
        data.setdefault("schema_version", MIGRATION_SCHEMA_VERSION)
        data.setdefault("ssot", SSOT_MODE)
        data.setdefault("tickers", {})
        return data
    except (json.JSONDecodeError, OSError):
        return {"schema_version": MIGRATION_SCHEMA_VERSION, "ssot": SSOT_MODE, "tickers": {}}


def save_migration_state(state: dict[str, Any]) -> None:
    path = migration_state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        **state,
        "schema_version": MIGRATION_SCHEMA_VERSION,
        "ssot": SSOT_MODE,
        "last_run_at": _now_iso(),
    }
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _migration_days() -> int | None:
    raw = os.getenv("HUB_NEWS_MIGRATION_DAYS", "0").strip()
    if not raw or raw.lower() in {"all", "0", "none"}:
        return None
    try:
        return max(0, int(raw))
    except ValueError:
        return None


def _legacy_record_count(*, ticker: str | None = None) -> int:
    records = iter_legacy_verified_records(ticker=ticker, limit=100_000)
    return len(records)


def _record_content_differs_from_event(record: dict[str, Any], event_id: str) -> bool:
    from trade_integrations.hub_storage.news_events_store import get_event

    event = get_event(event_id)
    if not event:
        return True
    legacy = str(record.get("content_summary") or record.get("title") or "").strip()
    current = str(event.get("content") or event.get("title") or "").strip()
    return legacy != current


def migrate_records_to_events(
    *,
    ticker: str = "NIFTY",
    since: str | None = None,
    limit: int = 5000,
    dry_run: bool = False,
    only_missing: bool = True,
    resync_stale: bool = True,
    source: str = "legacy",
) -> dict[str, Any]:
    """Copy legacy ``records.parquet`` rows into ``events.parquet``."""
    sym = ticker.strip().upper()
    records = iter_legacy_verified_records(ticker=sym, limit=limit)
    if since:
        records = [
            rec
            for rec in records
            if str(rec.get("published_at") or "")[:10] >= since[:10]
        ]
    known_ids = existing_event_ids(ticker=sym) if only_missing or resync_stale else set()

    upserted = 0
    skipped = 0
    resynced = 0
    for record in records:
        event = event_from_verified_record(record)
        event_id = str(event.event_id or "").strip()
        if not event_id:
            skipped += 1
            continue

        if only_missing and event_id in known_ids:
            if resync_stale and _record_content_differs_from_event(record, event_id):
                if dry_run:
                    resynced += 1
                else:
                    upsert_event(event)
                    resynced += 1
            else:
                skipped += 1
            continue

        if dry_run:
            upserted += 1
            continue

        upsert_event(event)
        upserted += 1
        known_ids.add(event_id)

    return {
        "ticker": sym,
        "since": since,
        "source": source,
        "legacy_records": len(records),
        "events_before": count_events(ticker=sym),
        "upserted": upserted,
        "resynced": resynced,
        "skipped": skipped,
        "dry_run": dry_run,
        "events_after": count_events(ticker=sym) if not dry_run else count_events(ticker=sym),
    }


def archive_legacy_records() -> dict[str, Any]:
    """Move legacy records.parquet to timestamped archive and leave empty shell."""
    path = verified_records_path()
    if not path.is_file():
        return {"archived": False, "reason": "missing"}
    frame = iter_legacy_verified_records(limit=100_000)
    if not frame:
        write_dataframe(pd.DataFrame(columns=list(_RECORD_COLUMNS)), path)
        return {"archived": False, "reason": "empty"}

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    archive = path.with_name(f"records.archived.{stamp}.parquet")
    shutil.copy2(path, archive)
    write_dataframe(pd.DataFrame(columns=list(_RECORD_COLUMNS)), path)
    return {"archived": True, "archive_path": str(archive), "rows": len(frame)}


def finalize_events_ssot(*, dry_run: bool = False) -> dict[str, Any]:
    """One-time cutover: migrate all legacy rows, archive records.parquet."""
    ensure_events_storage()
    state = load_migration_state()
    if state.get("legacy_archived") and not dry_run:
        return {"skipped": True, "reason": "already_finalized", "state": state}

    legacy_count = _legacy_record_count()
    tickers = list_verified_tickers()
    if not tickers and legacy_count:
        tickers = sorted({str(r.get("ticker") or "NIFTY").upper() for r in iter_legacy_verified_records(limit=100_000)})

    migration_results: dict[str, Any] = {}
    total_upserted = 0
    for sym in tickers or ["NIFTY"]:
        result = migrate_records_to_events(
            ticker=sym,
            since=None,
            limit=int(os.getenv("HUB_NEWS_MIGRATION_LIMIT", "10000")),
            dry_run=dry_run,
            only_missing=False,
            resync_stale=False,
        )
        migration_results[sym] = result
        total_upserted += int(result.get("upserted") or 0)

    archive_info: dict[str, Any] = {"archived": False}
    if not dry_run and legacy_count:
        archive_info = archive_legacy_records()

    if not dry_run:
        state["legacy_archived"] = True
        state["legacy_rows_migrated"] = legacy_count
        state["archive"] = archive_info
        state["ssot"] = SSOT_MODE
        save_migration_state(state)
        try:
            from trade_integrations.hub_storage.news_event_index import rebuild_event_index

            rebuild_event_index()
        except Exception as exc:
            logger.warning("event index rebuild after cutover skipped: %s", exc)

    return {
        "dry_run": dry_run,
        "legacy_rows": legacy_count,
        "upserted": total_upserted,
        "tickers": migration_results,
        "archive": archive_info,
    }


def events_ssot_finalized() -> bool:
    """True after one-time records→events cutover completed."""
    state = load_migration_state()
    return bool(state.get("legacy_archived"))


def needs_news_migration(*, ticker: str | None = None) -> bool:
    """True when legacy records.parquet still has unmigrated rows."""
    legacy = _legacy_record_count(ticker=ticker)
    return legacy > 0


def ensure_hub_news_migrations(
    *,
    ticker: str | None = None,
    dry_run: bool = False,
    force: bool = False,
) -> dict[str, Any]:
    """Run pending records→events migrations and finalize SSOT cutover."""
    ensure_events_storage()
    state = load_migration_state()

    if needs_news_migration(ticker=ticker):
        finalize = finalize_events_ssot(dry_run=dry_run)
        if not dry_run:
            state = load_migration_state()
    else:
        finalize = {"skipped": True, "reason": "no_legacy_rows"}

    if state.get("legacy_archived") and not force:
        return {
            "schema_version": MIGRATION_SCHEMA_VERSION,
            "ssot": SSOT_MODE,
            "dry_run": dry_run,
            "finalize": finalize,
            "incremental": {"skipped": True, "reason": "ssot_cutover_complete"},
            "state_path": str(migration_state_path()),
        }

    days = _migration_days()
    since = None
    if days is not None and days > 0:
        since = (datetime.now(timezone.utc).date() - timedelta(days=days)).isoformat()

    tickers = [ticker.strip().upper()] if ticker else list_verified_tickers()
    if not tickers:
        tickers = ["NIFTY"]

    ticker_results: dict[str, Any] = {}
    total_upserted = 0
    total_resynced = 0

    for sym in tickers:
        if _legacy_record_count(ticker=sym) == 0:
            ticker_results[sym] = {"skipped": True, "reason": "no_legacy_rows"}
            continue
        result = migrate_records_to_events(
            ticker=sym,
            since=since,
            limit=int(os.getenv("HUB_NEWS_MIGRATION_LIMIT", "10000")),
            dry_run=dry_run,
            only_missing=not force,
            resync_stale=not dry_run,
        )
        ticker_results[sym] = result
        total_upserted += int(result.get("upserted") or 0)
        total_resynced += int(result.get("resynced") or 0)
        state.setdefault("tickers", {})[sym] = {
            "last_at": _now_iso(),
            "legacy_records": result.get("legacy_records"),
            "events_count": int(result.get("events_after") or count_events(ticker=sym)),
            "upserted": int(result.get("upserted") or 0),
            "resynced": int(result.get("resynced") or 0),
        }

    if not dry_run:
        save_migration_state(state)

    summary = {
        "schema_version": MIGRATION_SCHEMA_VERSION,
        "ssot": SSOT_MODE,
        "dry_run": dry_run,
        "force": force,
        "finalize": finalize,
        "tickers_processed": len(ticker_results),
        "upserted": total_upserted,
        "resynced": total_resynced,
        "legacy_remaining": _legacy_record_count(),
        "tickers": ticker_results,
        "state_path": str(migration_state_path()),
    }
    if total_upserted or total_resynced or finalize.get("upserted"):
        logger.info(
            "hub news migration: upserted=%s resynced=%s legacy_remaining=%s",
            total_upserted,
            total_resynced,
            summary["legacy_remaining"],
        )
    return summary
