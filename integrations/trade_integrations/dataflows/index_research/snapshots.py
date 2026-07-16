"""List versioned index research snapshots from hub history folder."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from trade_integrations.context.hub import get_hub_dir


def list_index_research_snapshots(ticker: str = "NIFTY", *, limit: int = 10) -> list[dict[str, Any]]:
    """Return recent saved index research snapshots (newest first)."""
    sym = ticker.strip().upper()
    history_dir = get_hub_dir() / sym / "index_research" / "history"
    if not history_dir.is_dir():
        return []

    paths = sorted(history_dir.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    rows: list[dict[str, Any]] = []
    for path in paths[: max(1, limit)]:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        pred = payload.get("prediction") or {}
        rows.append(
            {
                "as_of": payload.get("as_of"),
                "spot": payload.get("spot"),
                "expected_return_pct": pred.get("expected_return_pct"),
                "bottom_up_return_pct": pred.get("bottom_up_return_pct"),
                "macro_delta_pct": pred.get("macro_delta_pct"),
                "view": pred.get("view"),
                "constituent_count": len(payload.get("constituent_signals") or []),
                "path": str(path.name),
                "horizon_days": (payload.get("horizon") or {}).get("days"),
                "range_low": (pred.get("range") or {}).get("low"),
                "range_high": (pred.get("range") or {}).get("high"),
            }
        )
    return rows
