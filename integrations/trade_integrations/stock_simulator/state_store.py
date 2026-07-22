"""Cross-process sim clock persistence (Trade watch steps ↔ OpenAlgo quotes)."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")


def _state_path() -> Path:
    root = Path(__file__).resolve().parents[3]
    return root / "log" / "sim_replay_state.json"


def load_sim_now(*, replay_date: str) -> datetime | None:
    path = _state_path()
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError):
        return None
    if str(data.get("replay_date") or "")[:10] != replay_date[:10]:
        return None
    raw = data.get("sim_now")
    if not raw:
        return None
    return datetime.fromisoformat(str(raw)).astimezone(IST)


def persist_sim_now(*, replay_date: str, sim_now: datetime) -> None:
    path = _state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload: dict[str, Any] = {
        "replay_date": replay_date[:10],
        "sim_now": sim_now.astimezone(IST).isoformat(),
    }
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
