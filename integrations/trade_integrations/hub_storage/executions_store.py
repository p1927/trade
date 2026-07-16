"""Materialize execution ledger JSON into hub trades parquet."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pandas as pd

from trade_integrations.context.hub import get_hub_dir
from trade_integrations.hub_storage.parquet_io import read_dataframe, write_dataframe

_EXECUTIONS_REL = Path("_data") / "trades" / "executions.parquet"
_FILLS_REL = Path("_data") / "trades" / "fills.parquet"

_EXECUTION_COLUMNS = (
    "execution_id",
    "widget_id",
    "underlying",
    "strategy",
    "legs_json",
    "prediction_view",
    "plan_spot",
    "executed_at",
    "closed_at",
    "status",
    "execution_mode",
    "broker_order_ids_json",
    "realized_pnl_inr",
    "net_max_loss",
)


def executions_parquet_path() -> Path:
    return get_hub_dir() / _EXECUTIONS_REL


def fills_parquet_path() -> Path:
    return get_hub_dir() / _FILLS_REL


def _entry_to_row(entry: dict[str, Any]) -> dict[str, Any]:
    legs = entry.get("legs") or entry.get("leg") or []
    broker_ids = entry.get("broker_order_ids") or []
    strategy = entry.get("recommended_name") or entry.get("strategy")
    return {
        "execution_id": entry.get("execution_id"),
        "widget_id": entry.get("widget_id"),
        "underlying": entry.get("underlying"),
        "strategy": strategy,
        "legs_json": json.dumps(legs, default=str),
        "prediction_view": entry.get("prediction_view"),
        "plan_spot": entry.get("plan_spot"),
        "executed_at": entry.get("executed_at"),
        "closed_at": entry.get("closed_at"),
        "status": entry.get("status"),
        "execution_mode": entry.get("execution_mode"),
        "broker_order_ids_json": json.dumps(broker_ids, default=str),
        "realized_pnl_inr": entry.get("realized_pnl_inr"),
        "net_max_loss": entry.get("net_max_loss"),
    }


def entries_to_dataframe(entries: list[dict[str, Any]]) -> pd.DataFrame:
    rows = [_entry_to_row(entry) for entry in entries if isinstance(entry, dict)]
    if not rows:
        return pd.DataFrame(columns=list(_EXECUTION_COLUMNS))
    return pd.DataFrame(rows, columns=list(_EXECUTION_COLUMNS))


def sync_executions_parquet(entries: list[dict[str, Any]]) -> None:
    """Rebuild executions.parquet from ledger entries (JSON remains source of truth)."""
    write_dataframe(entries_to_dataframe(entries), executions_parquet_path())


def sync_executions_from_ledger() -> int:
    """Load ledger.json and sync parquet; returns row count."""
    from trade_integrations.monitor.execution_ledger import load_ledger

    entries = load_ledger()
    sync_executions_parquet(entries)
    return len(entries)
