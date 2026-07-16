"""Export OpenAlgo sandbox fills into hub trades parquet."""

from __future__ import annotations

import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

import pandas as pd

from trade_integrations.env import load_trade_env, trade_repo_root
from trade_integrations.hub_storage.executions_store import fills_parquet_path
from trade_integrations.hub_storage.parquet_io import read_dataframe, write_dataframe

_FILLS_COLUMNS = (
    "timestamp",
    "symbol",
    "side",
    "qty",
    "price",
    "order_id",
    "trade_id",
    "exchange",
    "product",
    "source",
)


def _parse_sqlite_path(database_url: str, *, repo_root: Path) -> Path | None:
    url = database_url.strip()
    if not url:
        return None
    if url.startswith("sqlite:"):
        parsed = urlparse(url.replace("sqlite:///", "file:///", 1))
        raw = unquote(parsed.path or "")
        if raw.startswith("/") or (len(raw) > 1 and raw[1] == ":"):
            candidate = Path(raw)
        else:
            candidate = repo_root / "openalgo" / raw
        return candidate if candidate.is_file() else None
    return None


def resolve_sandbox_db_path() -> Path | None:
    """Locate OpenAlgo sandbox SQLite database."""
    load_trade_env()
    repo = Path(os.getenv("TRADE_STACK_ROOT") or trade_repo_root())

    explicit = os.getenv("OPENALGO_SANDBOX_DB", "").strip()
    if explicit:
        path = Path(explicit)
        if path.is_file():
            return path

    default = repo / "openalgo" / "db" / "sandbox.db"
    if default.is_file():
        return default

    openalgo_env = repo / "openalgo" / ".env"
    if openalgo_env.is_file():
        for line in openalgo_env.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            if key.strip() != "SANDBOX_DATABASE_URL":
                continue
            parsed = _parse_sqlite_path(value.strip().strip("'\""), repo_root=repo)
            if parsed is not None:
                return parsed

    url = os.getenv("SANDBOX_DATABASE_URL", "").strip()
    if url:
        return _parse_sqlite_path(url, repo_root=repo)
    return None


def _read_sandbox_trades(db_path: Path) -> list[dict[str, Any]]:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            """
            SELECT tradeid, orderid, symbol, exchange, action, quantity, price,
                   product, strategy, trade_timestamp
            FROM sandbox_trades
            ORDER BY trade_timestamp ASC, id ASC
            """
        ).fetchall()
    finally:
        conn.close()
    return [dict(row) for row in rows]


def _trade_rows_to_frame(trades: list[dict[str, Any]], *, source: str) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for trade in trades:
        ts = trade.get("trade_timestamp")
        if isinstance(ts, datetime):
            timestamp = ts.astimezone(timezone.utc).isoformat()
        else:
            timestamp = str(ts) if ts is not None else None
        rows.append(
            {
                "timestamp": timestamp,
                "symbol": trade.get("symbol"),
                "side": trade.get("action"),
                "qty": trade.get("quantity"),
                "price": float(trade.get("price") or 0),
                "order_id": trade.get("orderid"),
                "trade_id": trade.get("tradeid"),
                "exchange": trade.get("exchange"),
                "product": trade.get("product"),
                "source": source,
            }
        )
    if not rows:
        return pd.DataFrame(columns=list(_FILLS_COLUMNS))
    return pd.DataFrame(rows, columns=list(_FILLS_COLUMNS))


def export_openalgo_fills(*, dry_run: bool = False) -> dict[str, Any]:
    """Append new sandbox trades to fills.parquet (dedupe by trade_id)."""
    from trade_integrations.hub_storage.executions_store import sync_executions_from_ledger

    db_path = resolve_sandbox_db_path()
    summary: dict[str, Any] = {
        "status": "ok",
        "sandbox_db": str(db_path) if db_path else None,
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "new_rows": 0,
        "total_rows": 0,
    }
    if db_path is None:
        summary["status"] = "skipped"
        summary["reason"] = "sandbox_db_not_found"
        return summary

    trades = _read_sandbox_trades(db_path)
    incoming = _trade_rows_to_frame(trades, source="openalgo_sandbox")
    if incoming.empty:
        summary["reason"] = "no_sandbox_trades"
        sync_executions_from_ledger()
        return summary

    dest = fills_parquet_path()
    existing = read_dataframe(dest)
    if existing.empty:
        merged = incoming
    else:
        if "trade_id" not in existing.columns:
            existing["trade_id"] = None
        known = {str(v) for v in existing["trade_id"].dropna().astype(str)}
        mask = ~incoming["trade_id"].astype(str).isin(known)
        new_rows = incoming[mask]
        summary["new_rows"] = int(len(new_rows))
        merged = pd.concat([existing, new_rows], ignore_index=True) if len(new_rows) else existing

    summary["total_rows"] = int(len(merged))
    if not dry_run:
        write_dataframe(merged, dest)
        sync_executions_from_ledger()
    return summary
