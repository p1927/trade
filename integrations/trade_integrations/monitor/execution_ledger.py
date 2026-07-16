"""Persist executed trade-plan widgets for position-aware thesis monitoring."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from trade_integrations.context.hub import get_hub_dir


def _ledger_path() -> Path:
    return get_hub_dir() / "_data" / "executions" / "ledger.json"


def load_ledger() -> list[dict[str, Any]]:
    """Load all execution ledger entries."""
    path = _ledger_path()
    if not path.is_file():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []
    entries = payload.get("entries") if isinstance(payload, dict) else payload
    if not isinstance(entries, list):
        return []
    return [entry for entry in entries if isinstance(entry, dict)]


def save_ledger(entries: list[dict[str, Any]]) -> None:
    """Persist ledger entries to hub storage."""
    path = _ledger_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"entries": entries}, indent=2, default=str),
        encoding="utf-8",
    )


def _new_execution_id(underlying: str) -> str:
    return f"ex_{underlying.strip().upper()}_{uuid.uuid4().hex[:12]}"


def _extract_broker_order_ids(results: list[dict[str, Any]] | None) -> list[str]:
    ids: list[str] = []
    for row in results or []:
        if not isinstance(row, dict):
            continue
        for key in ("orderid", "order_id", "id", "orderId"):
            value = row.get(key)
            if value is not None and str(value).strip():
                ids.append(str(value).strip())
                break
    return ids


def record_execution(
    *,
    widget_id: str,
    underlying: str,
    legs: list[dict[str, Any]],
    prediction_view: str | None,
    recommended_name: str | None,
    scenarios: list[dict[str, Any]] | None,
    broker_order_ids: list[str] | None = None,
    plan_spot: float | None = None,
    net_max_loss: float | None = None,
    execution_mode: str = "live",
    status: str = "open",
) -> dict[str, Any]:
    """Append a ledger entry for a successfully executed trade plan."""
    symbol = underlying.strip().upper()
    entry: dict[str, Any] = {
        "execution_id": _new_execution_id(symbol),
        "widget_id": widget_id,
        "underlying": symbol,
        "legs": legs or [],
        "prediction_view": prediction_view,
        "recommended_name": recommended_name,
        "scenarios": scenarios or [],
        "executed_at": datetime.now(timezone.utc).isoformat(),
        "status": status,
        "broker_order_ids": broker_order_ids or [],
        "plan_spot": plan_spot,
        "net_max_loss": net_max_loss,
        "execution_mode": execution_mode,
    }
    entries = load_ledger()
    entries.append(entry)
    save_ledger(entries)
    return entry


def get_ledger_entry(widget_id: str) -> dict[str, Any] | None:
    """Return the most recent ledger entry for a widget id."""
    matches = [entry for entry in load_ledger() if entry.get("widget_id") == widget_id]
    if not matches:
        return None
    return matches[-1]


def get_ledger_entry_by_execution_id(execution_id: str) -> dict[str, Any] | None:
    for entry in load_ledger():
        if entry.get("execution_id") == execution_id:
            return entry
    return None


def list_open_entries() -> list[dict[str, Any]]:
    """Return ledger entries with status open."""
    return [entry for entry in load_ledger() if entry.get("status") == "open"]


def list_open_by_underlying(underlying: str) -> list[dict[str, Any]]:
    """Return open ledger entries for an underlying symbol."""
    symbol = underlying.strip().upper()
    return [
        entry
        for entry in list_open_entries()
        if str(entry.get("underlying", "")).upper() == symbol
    ]


def has_open_position_for_underlying(underlying: str) -> bool:
    """Return True when the ledger has at least one open entry for the underlying."""
    return bool(list_open_by_underlying(underlying))


def close_ledger_entry(widget_id: str) -> bool:
    """Mark the latest ledger entry for a widget as closed."""
    entries = load_ledger()
    updated = False
    for index in range(len(entries) - 1, -1, -1):
        if entries[index].get("widget_id") != widget_id:
            continue
        if entries[index].get("status") == "closed":
            return False
        entries[index]["status"] = "closed"
        updated = True
        break
    if updated:
        save_ledger(entries)
    return updated


def _widget_legs(widget: dict[str, Any]) -> list[dict[str, Any]]:
    recommended = widget.get("recommended") or {}
    legs = recommended.get("legs")
    if isinstance(legs, list) and legs:
        return legs
    name = widget.get("agent_recommended_strategy") or recommended.get("name")
    variants = widget.get("strategy_variants") or {}
    if name and isinstance(variants.get(name), dict):
        variant_legs = (variants[name].get("recommended") or {}).get("legs")
        if isinstance(variant_legs, list):
            return variant_legs
    return []


def _widget_net_max_loss(widget: dict[str, Any]) -> float | None:
    for source in (
        widget.get("recommended") or {},
        widget.get("payoff") or {},
    ):
        for key in ("net_max_loss", "max_loss"):
            value = source.get(key)
            if value is None:
                continue
            try:
                return float(value)
            except (TypeError, ValueError):
                continue
    return None


def record_execution_from_widget(
    widget: dict[str, Any],
    results: list[dict[str, Any]] | None,
    *,
    execution_mode: str = "live",
) -> dict[str, Any]:
    """Build and store a ledger entry from a trade widget and basket results."""
    widget_id = str(widget.get("widget_id") or "").strip()
    underlying = str(widget.get("underlying") or "").strip().upper()
    if not widget_id or not underlying:
        raise ValueError("widget must include widget_id and underlying")

    prediction = widget.get("prediction") or {}
    return record_execution(
        widget_id=widget_id,
        underlying=underlying,
        legs=_widget_legs(widget),
        prediction_view=prediction.get("view"),
        recommended_name=(
            (widget.get("recommended") or {}).get("name")
            or widget.get("agent_recommended_strategy")
        ),
        scenarios=widget.get("scenarios") or [],
        broker_order_ids=_extract_broker_order_ids(results),
        plan_spot=widget.get("spot"),
        net_max_loss=_widget_net_max_loss(widget),
        execution_mode=execution_mode,
    )


def _normalize_position_rows(position_book: Any) -> list[dict[str, Any]]:
    if isinstance(position_book, str):
        try:
            position_book = json.loads(position_book)
        except json.JSONDecodeError:
            return []
    if not isinstance(position_book, dict):
        return []
    rows = position_book.get("data")
    if not isinstance(rows, list):
        return []
    return [row for row in rows if isinstance(row, dict)]


def match_positions_for_entry(
    ledger_entry: dict[str, Any],
    position_book: Any,
) -> tuple[list[dict[str, Any]], float | None]:
    """Match ledger legs to position-book rows and sum unrealized P&L."""
    legs = ledger_entry.get("leg") or ledger_entry.get("legs") or []
    leg_symbols = {
        str(leg.get("symbol", "")).upper()
        for leg in legs
        if isinstance(leg, dict) and leg.get("symbol")
    }
    if not leg_symbols:
        return [], None

    matched: list[dict[str, Any]] = []
    total_pnl = 0.0
    found_pnl = False
    for row in _normalize_position_rows(position_book):
        symbol = str(row.get("symbol", "")).upper()
        if symbol not in leg_symbols:
            continue
        matched.append(row)
        pnl = row.get("pnl")
        if pnl is None:
            pnl = row.get("unrealised") or row.get("unrealized")
        if pnl is None:
            continue
        try:
            total_pnl += float(pnl)
            found_pnl = True
        except (TypeError, ValueError):
            continue

    return matched, total_pnl if found_pnl else None


def fetch_position_book() -> dict[str, Any] | None:
    """Fetch OpenAlgo position book; None when unavailable."""
    import os

    import requests

    host = os.getenv("OPENALGO_HOST", "http://127.0.0.1:5001").rstrip("/")
    api_key = os.getenv("OPENALGO_API_KEY", "").strip()
    if not api_key:
        return None
    try:
        response = requests.post(
            f"{host}/api/v1/positionbook",
            json={"apikey": api_key},
            timeout=15,
        )
        if response.ok:
            body = response.json()
            return body if isinstance(body, dict) else None
    except Exception:
        return None
    return None
