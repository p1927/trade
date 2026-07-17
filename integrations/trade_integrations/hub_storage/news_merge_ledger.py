"""Append-only log of hub news merge events for audit and backtest replay."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from trade_integrations.context.hub import get_hub_dir

_LEDGER_REL = Path("_data") / "news_verified" / "merge_ledger.jsonl"


def merge_ledger_path() -> Path:
    return get_hub_dir() / _LEDGER_REL


def append_merge_event(
    *,
    ticker: str,
    event_id: str,
    canonical_story_id: str,
    merged_story_ids: list[str],
    ref_count: int,
    reason: str,
    title: str = "",
) -> None:
    """Record one merge (staging update or compaction)."""
    path = merge_ledger_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    row = {
        "at": datetime.now(timezone.utc).isoformat(),
        "ticker": ticker.strip().upper(),
        "event_id": event_id,
        "canonical_story_id": canonical_story_id,
        "merged_story_ids": sorted({str(s).strip() for s in merged_story_ids if str(s).strip()}),
        "ref_count": int(ref_count),
        "reason": reason,
        "title": (title or "")[:200],
    }
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, ensure_ascii=False) + "\n")


def list_merge_events(*, ticker: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
    path = merge_ledger_path()
    if not path.is_file():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if ticker and str(row.get("ticker") or "").upper() != ticker.strip().upper():
            continue
        rows.append(row)
    return rows[-limit:]
