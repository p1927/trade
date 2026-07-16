"""Append-only audit ledger for autonomous paper trading (mirrors live/audit.py)."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from trade_integrations.context.hub import get_hub_dir

PaperActionKind = Literal[
    "session_started",
    "session_stopped",
    "decision_recorded",
    "basket_executed",
    "positions_closed",
    "reconcile_warning",
    "market_feedback",
    "turn_dispatched",
    "halt",
]

_LEDGER = "audit.jsonl"


def audit_ledger_path() -> Path:
    return get_hub_dir() / "_data" / "auto_paper" / _LEDGER


def write_paper_action(
    kind: PaperActionKind,
    *,
    outcome: str = "ok",
    detail: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Append one paper action record; return record with audit_id for SSE relay."""
    record: dict[str, Any] = {
        "audit_id": f"pa_{uuid.uuid4().hex[:16]}",
        "kind": kind,
        "outcome": outcome,
        "at": datetime.now(timezone.utc).isoformat(),
        "mode": "paper",
        "detail": detail or {},
        "paper_action": True,
    }
    path = audit_ledger_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, default=str) + "\n")
    return record


def load_paper_action(audit_id: str) -> dict[str, Any] | None:
    path = audit_ledger_path()
    if not path.is_file():
        return None
    for line in reversed(path.read_text(encoding="utf-8").splitlines()):
        if audit_id not in line:
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(record, dict) and record.get("audit_id") == audit_id:
            return record
    return None
