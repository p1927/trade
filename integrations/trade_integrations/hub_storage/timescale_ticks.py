"""TimescaleDB hot tier for sub-minute market ticks (Phase 12)."""

from __future__ import annotations

import logging
import os
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import pandas as pd

from trade_integrations.context.hub import get_hub_dir
from trade_integrations.env import load_trade_env
from trade_integrations.hub_storage.parquet_io import concat_dataframes, read_dataframe, upsert_by_keys, write_dataframe

logger = logging.getLogger(__name__)

_TICKS_DAILY_REL = Path("_data") / "ticks" / "daily"
_ENABLED_ENV = "TIMESCALE_ENABLED"
_URL_ENV = "TIMESCALE_DATABASE_URL"
_HOT_RETENTION_DAYS_ENV = "TIMESCALE_HOT_RETENTION_DAYS"


def _default_timescale_url() -> str:
    from trade_integrations.stack_ports import timescale_database_url as ports_timescale_url

    load_trade_env()
    return ports_timescale_url()


def is_timescale_enabled() -> bool:
    load_trade_env()
    return os.getenv(_ENABLED_ENV, "").strip().lower() in {"1", "true", "yes", "on"}


def timescale_database_url() -> str:
    load_trade_env()
    return os.getenv(_URL_ENV, _default_timescale_url()).strip() or _default_timescale_url()


def hot_retention_days() -> int:
    try:
        return max(1, int(os.getenv(_HOT_RETENTION_DAYS_ENV, "7")))
    except ValueError:
        return 7


def ticks_daily_dir() -> Path:
    return get_hub_dir() / _TICKS_DAILY_REL


def _connect():
    import psycopg

    return psycopg.connect(timescale_database_url())


def ensure_schema() -> None:
    """Create hypertable and indexes if missing."""
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute("CREATE EXTENSION IF NOT EXISTS timescaledb CASCADE;")
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS market_ticks (
                    ts TIMESTAMPTZ NOT NULL,
                    symbol TEXT NOT NULL,
                    exchange TEXT,
                    price DOUBLE PRECISION NOT NULL,
                    volume DOUBLE PRECISION,
                    open DOUBLE PRECISION,
                    high DOUBLE PRECISION,
                    low DOUBLE PRECISION,
                    oi DOUBLE PRECISION,
                    source TEXT NOT NULL DEFAULT 'openalgo'
                );
                """
            )
            cur.execute(
                """
                SELECT create_hypertable(
                    'market_ticks', 'ts',
                    chunk_time_interval => INTERVAL '1 day',
                    if_not_exists => TRUE
                );
                """
            )
            cur.execute(
                """
                SELECT set_chunk_time_interval('market_ticks', INTERVAL '1 day');
                """
            )
            cur.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_market_ticks_symbol_ts
                ON market_ticks (symbol, ts DESC);
                """
            )
        conn.commit()


def record_quote_snapshot(
    *,
    symbol: str,
    exchange: str,
    price: float,
    volume: float | None = None,
    open_: float | None = None,
    high: float | None = None,
    low: float | None = None,
    oi: float | None = None,
    source: str = "openalgo",
    ts: datetime | None = None,
) -> bool:
    """Append one tick row when Timescale is enabled."""
    if not is_timescale_enabled():
        return False
    stamp = ts or datetime.now(timezone.utc)
    try:
        ensure_schema()
        with _connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO market_ticks
                        (ts, symbol, exchange, price, volume, open, high, low, oi, source)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        stamp,
                        symbol.upper(),
                        exchange.upper(),
                        float(price),
                        volume,
                        open_,
                        high,
                        low,
                        oi,
                        source,
                    ),
                )
            conn.commit()
        return True
    except Exception:
        logger.debug("timescale tick insert failed", exc_info=True)
        return False


def record_quote_snapshots(quotes: dict[str, Any], *, source: str = "openalgo_watch") -> int:
    """Batch-insert quote snapshots from bridge poll results."""
    if not is_timescale_enabled() or not quotes:
        return 0
    inserted = 0
    for snap in quotes.values():
        if hasattr(snap, "ltp"):
            ok = record_quote_snapshot(
                symbol=snap.symbol,
                exchange=snap.exchange,
                price=snap.ltp,
                volume=snap.volume,
                open_=snap.open,
                high=snap.high,
                low=snap.low,
                oi=snap.oi,
                source=source,
                ts=datetime.fromisoformat(snap.fetched_at.replace("Z", "+00:00"))
                if isinstance(snap.fetched_at, str)
                else None,
            )
        elif isinstance(snap, dict):
            ltp = snap.get("ltp") or snap.get("price")
            if ltp is None:
                continue
            ok = record_quote_snapshot(
                symbol=str(snap.get("symbol") or ""),
                exchange=str(snap.get("exchange") or "NSE"),
                price=float(ltp),
                volume=snap.get("volume"),
                open_=snap.get("open"),
                high=snap.get("high"),
                low=snap.get("low"),
                oi=snap.get("oi"),
                source=source,
            )
        else:
            continue
        if ok:
            inserted += 1
    return inserted


def export_ticks_day(day: str, *, delete_after_export: bool = False) -> dict[str, Any]:
    """Export one calendar day of ticks to hub parquet."""
    day_start = datetime.fromisoformat(day).replace(tzinfo=timezone.utc)
    day_end = day_start + timedelta(days=1)
    summary: dict[str, Any] = {"day": day, "exported_rows": 0, "status": "skipped"}
    if not is_timescale_enabled():
        summary["reason"] = "timescale_disabled"
        return summary
    try:
        with _connect() as conn:
            df = pd.read_sql(
                """
                SELECT ts, symbol, exchange, price, volume, open, high, low, oi, source
                FROM market_ticks
                WHERE ts >= %s AND ts < %s
                ORDER BY ts ASC
                """,
                conn,
                params=(day_start, day_end),
            )
    except Exception as exc:
        summary["status"] = "error"
        summary["error"] = str(exc)
        return summary

    if df.empty:
        summary["status"] = "ok"
        summary["reason"] = "no_rows"
        return summary

    dest = ticks_daily_dir() / f"{day}.parquet"
    existing = read_dataframe(dest)
    merged = upsert_by_keys(
        existing,
        df,
        dedupe_keys=["ts", "symbol", "exchange", "source"],
    )
    write_dataframe(merged, dest)
    summary["exported_rows"] = int(len(df))
    summary["total_rows"] = int(len(merged))
    summary["path"] = str(dest)
    summary["status"] = "ok"

    if delete_after_export:
        try:
            with _connect() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "DELETE FROM market_ticks WHERE ts >= %s AND ts < %s",
                        (day_start, day_end),
                    )
                conn.commit()
            summary["deleted_hot_rows"] = summary["exported_rows"]
        except Exception as exc:
            summary["delete_error"] = str(exc)
    return summary


def export_and_prune_hot_ticks(*, day: str | None = None) -> dict[str, Any]:
    """Export yesterday (or given day) and prune hot tier beyond retention window."""
    if day is None:
        day = (datetime.now(timezone.utc).date() - timedelta(days=1)).isoformat()
    export_summary = export_ticks_day(day, delete_after_export=True)
    prune_summary: dict[str, Any] = {"status": "skipped"}
    if not is_timescale_enabled():
        return {"export": export_summary, "prune": prune_summary}

    cutoff = datetime.now(timezone.utc) - timedelta(days=hot_retention_days())
    try:
        with _connect() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM market_ticks WHERE ts < %s", (cutoff,))
                deleted = cur.rowcount
            conn.commit()
        prune_summary = {"status": "ok", "cutoff": cutoff.isoformat(), "deleted_rows": deleted}
    except Exception as exc:
        prune_summary = {"status": "error", "error": str(exc)}
    return {"export": export_summary, "prune": prune_summary}


def timescale_health() -> dict[str, Any]:
    """Return connection and row-count health for ops checks."""
    if not is_timescale_enabled():
        return {"enabled": False, "ok": True, "reason": "disabled"}
    try:
        ensure_schema()
        with _connect() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) FROM market_ticks")
                count = cur.fetchone()[0]
                cur.execute("SELECT MAX(ts) FROM market_ticks")
                latest = cur.fetchone()[0]
                cur.execute("SELECT pg_is_in_recovery()")
                in_recovery = bool(cur.fetchone()[0])
                chunk_count = None
                try:
                    cur.execute(
                        """
                        SELECT COUNT(*)
                        FROM timescaledb_information.chunks
                        WHERE hypertable_name = 'market_ticks'
                        """
                    )
                    chunk_count = int(cur.fetchone()[0] or 0)
                except Exception:
                    pass
        out: dict[str, Any] = {
            "enabled": True,
            "ok": not in_recovery,
            "row_count": int(count or 0),
            "latest_ts": latest.isoformat() if latest else None,
            "database": urlparse(timescale_database_url()).path.lstrip("/"),
            "in_recovery": in_recovery,
        }
        if chunk_count is not None:
            out["chunk_count"] = chunk_count
            if chunk_count > 50:
                out["chunk_warning"] = (
                    f"{chunk_count} chunks — consider larger chunk interval or export/prune"
                )
        if in_recovery:
            out["reason"] = "wal_recovery_in_progress"
        return out
    except Exception as exc:
        return {"enabled": True, "ok": False, "error": str(exc)}
