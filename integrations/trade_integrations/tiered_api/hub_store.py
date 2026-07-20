"""Hub-backed response cache for tiered API calls."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from trade_integrations.context.hub import get_hub_dir
from trade_integrations.tiered_api.registry import hub_ttl_hours

logger = logging.getLogger(__name__)

_HUB_REL = Path("_data") / "tiered_api"


def _hub_root() -> Path:
    root = get_hub_dir() / _HUB_REL
    root.mkdir(parents=True, exist_ok=True)
    return root


def _cache_path(source: str, req_hash: str) -> Path:
    return _hub_root() / "cache" / source.strip().lower() / f"{req_hash}.json"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_cached(
    source: str,
    req_hash: str,
    *,
    force: bool = False,
    allow_stale: bool = False,
) -> dict[str, Any] | None:
    """Return cached payload dict or None if miss/stale."""
    if force:
        return None
    path = _cache_path(source, req_hash)
    if not path.is_file():
        return None
    try:
        envelope = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(envelope, dict):
        return None
    fetched_at = envelope.get("fetched_at")
    data = envelope.get("data")
    if data is None or not fetched_at:
        return None
    try:
        ts = datetime.fromisoformat(str(fetched_at))
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
    except ValueError:
        return None
    ttl_h = hub_ttl_hours(source)
    if ttl_h <= 0 and not allow_stale:
        return None
    age = datetime.now(timezone.utc) - ts
    if ttl_h > 0 and age > timedelta(hours=ttl_h) and not allow_stale:
        return None
    return envelope


def save_cached(
    source: str,
    req_hash: str,
    data: Any,
    *,
    request_meta: dict[str, Any] | None = None,
) -> Path:
    path = _cache_path(source, req_hash)
    path.parent.mkdir(parents=True, exist_ok=True)
    envelope = {
        "source": source.strip().lower(),
        "req_hash": req_hash,
        "fetched_at": _now_iso(),
        "request": request_meta or {},
        "data": data,
    }
    path.write_text(json.dumps(envelope, indent=2, default=str), encoding="utf-8")
    _update_manifest(source, req_hash, envelope)
    return path


def _manifest_path() -> Path:
    return _hub_root() / "manifest.json"


def _update_manifest(source: str, req_hash: str, envelope: dict[str, Any]) -> None:
    path = _manifest_path()
    try:
        manifest = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {"entries": []}
    except (OSError, json.JSONDecodeError):
        manifest = {"entries": []}
    entries = [e for e in manifest.get("entries", []) if e.get("req_hash") != req_hash]
    entries.append(
        {
            "source": source.strip().lower(),
            "req_hash": req_hash,
            "fetched_at": envelope.get("fetched_at"),
            "path": str(Path("cache") / source.strip().lower() / f"{req_hash}.json"),
        }
    )
    manifest["entries"] = entries[-5000:]
    manifest["updated_at"] = _now_iso()
    try:
        path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    except OSError as exc:
        logger.debug("tiered_api manifest write failed: %s", exc)


def list_cache_entries(source: str | None = None) -> list[dict[str, Any]]:
    cache_dir = _hub_root() / "cache"
    if not cache_dir.is_dir():
        return []
    entries: list[dict[str, Any]] = []
    sources = [source.strip().lower()] if source else [p.name for p in cache_dir.iterdir() if p.is_dir()]
    for src in sources:
        src_dir = cache_dir / src
        if not src_dir.is_dir():
            continue
        for path in src_dir.glob("*.json"):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if isinstance(payload, dict):
                entries.append(payload)
    return entries
