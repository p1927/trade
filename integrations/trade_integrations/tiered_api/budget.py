"""Daily call budget ledger for tiered APIs (UTC day keys)."""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from trade_integrations.context.hub import get_hub_dir
from trade_integrations.tiered_api.errors import TieredApiBudgetExhausted
from trade_integrations.tiered_api.registry import daily_limit, get_spec

logger = logging.getLogger(__name__)

_HUB_REL = Path("_data") / "tiered_api" / "ledger"
_LEDGER_LOCK = Path("_data") / "tiered_api" / ".ledger.lock"


def _utc_date() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def _ledger_dir() -> Path:
    path = get_hub_dir() / _HUB_REL
    path.mkdir(parents=True, exist_ok=True)
    return path


def _ledger_path(source: str, date_key: str | None = None) -> Path:
    day = date_key or _utc_date()
    return _ledger_dir() / source.strip().lower() / f"{day}.json"


def _lock_path() -> Path:
    path = get_hub_dir() / _LEDGER_LOCK
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


class _FileLock:
    """Simple exclusive file lock for cross-process ledger updates."""

    def __init__(self) -> None:
        self._path = _lock_path()
        self._fd: int | None = None

    def __enter__(self) -> _FileLock:
        import fcntl

        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._fd = os.open(str(self._path), os.O_CREAT | os.O_RDWR)
        fcntl.flock(self._fd, fcntl.LOCK_EX)
        return self

    def __exit__(self, *args: object) -> None:
        import fcntl

        if self._fd is not None:
            fcntl.flock(self._fd, fcntl.LOCK_UN)
            os.close(self._fd)
            self._fd = None


def _load_ledger(source: str, date_key: str | None = None) -> dict[str, Any]:
    path = _ledger_path(source, date_key)
    if not path.is_file():
        limit = daily_limit(source)
        return {"date": date_key or _utc_date(), "source": source, "calls": 0, "limit": limit, "entries": []}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        limit = daily_limit(source)
        return {"date": date_key or _utc_date(), "source": source, "calls": 0, "limit": limit, "entries": []}
    if not isinstance(payload, dict):
        limit = daily_limit(source)
        return {"date": date_key or _utc_date(), "source": source, "calls": 0, "limit": limit, "entries": []}
    payload.setdefault("calls", 0)
    payload["limit"] = daily_limit(source)
    return payload


def _save_ledger(source: str, ledger: dict[str, Any], date_key: str | None = None) -> None:
    path = _ledger_path(source, date_key)
    path.parent.mkdir(parents=True, exist_ok=True)
    ledger["updated_at"] = datetime.now(timezone.utc).isoformat()
    path.write_text(json.dumps(ledger, indent=2), encoding="utf-8")


def get_budget_status(source: str) -> dict[str, Any]:
    """Return today's call count and limit for a source."""
    get_spec(source)
    ledger = _load_ledger(source)
    return {
        "source": source.strip().lower(),
        "date": ledger.get("date", _utc_date()),
        "calls": int(ledger.get("calls", 0)),
        "limit": int(ledger.get("limit", daily_limit(source))),
        "remaining": max(0, int(ledger.get("limit", daily_limit(source))) - int(ledger.get("calls", 0))),
    }


def check_budget_headroom(source: str) -> None:
    """Raise TieredApiBudgetExhausted if daily limit reached."""
    status = get_budget_status(source)
    if status["limit"] <= 0:
        return
    if status["calls"] >= status["limit"]:
        raise TieredApiBudgetExhausted(
            source,
            calls=status["calls"],
            limit=status["limit"],
        )


def record_call(source: str, req_hash: str, *, cache_hit: bool = False) -> dict[str, Any]:
    """Increment ledger after a successful vendor call (not cache hits)."""
    if cache_hit:
        return get_budget_status(source)
    with _FileLock():
        ledger = _load_ledger(source)
        ledger["calls"] = int(ledger.get("calls", 0)) + 1
        entries = list(ledger.get("entries") or [])
        entries.append(
            {
                "req_hash": req_hash,
                "at": datetime.now(timezone.utc).isoformat(),
            }
        )
        ledger["entries"] = entries[-1000:]
        _save_ledger(source, ledger)
        return get_budget_status(source)


def list_ledger_days(source: str) -> list[str]:
    src_dir = _ledger_dir() / source.strip().lower()
    if not src_dir.is_dir():
        return []
    return sorted(p.stem for p in src_dir.glob("*.json"))
