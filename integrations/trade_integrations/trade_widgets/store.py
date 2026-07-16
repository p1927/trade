"""Persist trade-plan widgets for MCP execute_basket and Vibe UI reload."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

_WIDGET_ID_RE = re.compile(r"^(?:tp|ts|ti)_[A-Z][A-Z0-9]*_[0-9a-f]{12}$")


def trade_widget_dir() -> Path:
    root = Path.home() / ".vibe-trading" / "trade_widgets"
    root.mkdir(parents=True, exist_ok=True)
    return root


def persist_trade_widget(widget: dict[str, Any]) -> str | None:
    """Write widget JSON to ~/.vibe-trading/trade_widgets/. Returns widget_id."""
    widget_id = str(widget.get("widget_id") or "").strip()
    if not widget_id or not _WIDGET_ID_RE.fullmatch(widget_id):
        return None
    path = trade_widget_dir() / f"{widget_id}.json"
    path.write_text(json.dumps(widget, indent=2, default=str), encoding="utf-8")
    return widget_id


def load_trade_widget(widget_id: str) -> dict[str, Any] | None:
    if not _WIDGET_ID_RE.fullmatch(widget_id or ""):
        return None
    path = trade_widget_dir() / f"{widget_id}.json"
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) and data.get("type") == "trade_plan.widget" else None


def _ticker_from_widget_id(widget_id: str) -> str | None:
    parts = widget_id.split("_")
    if len(parts) >= 3 and parts[0] in {"tp", "ts"}:
        return parts[1].strip().upper() or None
    return None


def ensure_trade_widget(widget_id: str) -> dict[str, Any]:
    """Load widget from disk or rebuild from hub cache for the underlying."""
    existing = load_trade_widget(widget_id)
    if existing is not None:
        return existing

    ticker = _ticker_from_widget_id(widget_id)
    if not ticker:
        raise ValueError(f"Widget not found: {widget_id}")

    from trade_integrations.dataflows.options_research.widget_payload import (
        build_options_trade_widget,
    )

    widget = build_options_trade_widget(ticker, refresh=False)
    if not isinstance(widget, dict) or not widget.get("widget_id"):
        raise ValueError(f"Could not rebuild trade widget for {ticker}")

    persist_trade_widget(widget)
    widget.setdefault("_recovered_from", widget_id)
    return widget
