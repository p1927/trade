"""Capture registry — which entities and factor groups persist proprietary hub data."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from trade_integrations.context.hub import get_hub_dir
from trade_integrations.dataflows.index_research.factor_catalog import NIFTY_FACTOR_CATALOG

CaptureTier = Literal["capture", "scalar", "ephemeral"]

_REGISTRY_VERSION = 1
_REGISTRY_REL = Path("_data") / "capture_registry.json"

_DEFAULT_ENTITY_ID = "NIFTY"
_DEFAULT_FACTOR_GROUPS = ("derivatives", "flows", "vol")
_DEFAULT_SCHEDULES = {
    "chain_intraday_cron": "0 10,13,15 * * 1-5",
    "factor_snapshot_cron": "0 18 * * *",
}
_DEFAULT_RETENTION = {
    "derivatives": 365,
    "flows": 365,
    "vix": 365,
    "ticks_hot": 7,
    "ticks_cold": 90,
    "history_cold": None,
}

# Tier A — proprietary / hard to replay
_TIER_A_KEYS = frozenset({
    "nifty_pcr",
    "fii_fut_long_short_ratio",
    "fii_net_5d",
    "dii_net_5d",
    "fpi_equity_net_usd",
    "fpi_debt_net_usd",
    "india_vix",
    "institutional_net_5d",
    "dii_absorption_ratio",
})

# Tier C — ephemeral unless explicitly captured via watch
_TIER_C_KEYS = frozenset({
    "index_spot_tick",
    "positionbook",
})

_SCALAR_SOURCE_MARKERS = ("yfinance", "fred", "tradingagents", "ohlcv history", "ridge", "logistic")


def registry_path() -> Path:
    return get_hub_dir() / _REGISTRY_REL


def factor_tier(key: str) -> CaptureTier:
    k = key.strip().lower()
    if k in _TIER_A_KEYS:
        return "capture"
    if k in _TIER_C_KEYS:
        return "ephemeral"
    entry = next((f for f in NIFTY_FACTOR_CATALOG if f.get("key") == k), None)
    if entry:
        src = str(entry.get("source") or "").lower()
        if any(m in src for m in _SCALAR_SOURCE_MARKERS):
            return "scalar"
        if "openalgo" in src or "nselib" in src or "nse" in src:
            return "capture"
    return "scalar"


def build_factor_tree() -> list[dict[str, Any]]:
    """Grouped factor catalog with capture tier labels for UI."""
    by_category: dict[str, list[dict[str, Any]]] = {}
    for item in NIFTY_FACTOR_CATALOG:
        cat = str(item.get("category") or "other")
        tier = factor_tier(str(item.get("key") or ""))
        by_category.setdefault(cat, []).append({**item, "tier": tier})
    return [
        {"category": cat, "factors": rows}
        for cat, rows in sorted(by_category.items())
    ]


def default_entity() -> dict[str, Any]:
    return {
        "id": _DEFAULT_ENTITY_ID,
        "kind": "index",
        "capture_enabled": True,
        "factor_groups": list(_DEFAULT_FACTOR_GROUPS),
        "schedules": dict(_DEFAULT_SCHEDULES),
        "retention_days": dict(_DEFAULT_RETENTION),
    }


def _stock_entity(entity_id: str) -> dict[str, Any]:
    return _validate_entity(
        {
            "id": entity_id,
            "kind": "equity",
            "capture_enabled": True,
            "factor_groups": ["derivatives"],
            "schedules": dict(_DEFAULT_SCHEDULES),
            "retention_days": {"derivatives": 90, "ticks": 7},
        }
    )


def default_registry() -> dict[str, Any]:
    return {
        "version": _REGISTRY_VERSION,
        "entities": [
            default_entity(),
            _validate_entity({**default_entity(), "id": "BANKNIFTY", "kind": "index"}),
            _validate_entity({**default_entity(), "id": "INDIAVIX", "kind": "index", "factor_groups": ["vol"]}),
            _stock_entity("RELIANCE"),
            _stock_entity("TCS"),
        ],
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }


def _validate_entity(raw: dict[str, Any]) -> dict[str, Any]:
    entity_id = str(raw.get("id") or _DEFAULT_ENTITY_ID).strip().upper()
    groups = raw.get("factor_groups")
    if not isinstance(groups, list) or not groups:
        groups = list(_DEFAULT_FACTOR_GROUPS)
    groups = [str(g).strip().lower() for g in groups if str(g).strip()]
    schedules = raw.get("schedules") if isinstance(raw.get("schedules"), dict) else {}
    retention = raw.get("retention_days") if isinstance(raw.get("retention_days"), dict) else {}
    merged_retention = dict(_DEFAULT_RETENTION)
    for key, val in retention.items():
        try:
            merged_retention[str(key)] = max(1, int(val))
        except (TypeError, ValueError):
            continue
    return {
        "id": entity_id,
        "kind": str(raw.get("kind") or "index"),
        "capture_enabled": bool(raw.get("capture_enabled", True)),
        "factor_groups": groups,
        "schedules": {**_DEFAULT_SCHEDULES, **schedules},
        "retention_days": merged_retention,
    }


def load_registry(*, create: bool = True) -> dict[str, Any]:
    path = registry_path()
    if path.is_file():
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(payload, dict) and isinstance(payload.get("entities"), list):
                entities = [_validate_entity(e) for e in payload["entities"] if isinstance(e, dict)]
                if entities:
                    payload["entities"] = entities
                    payload["version"] = _REGISTRY_VERSION
                    return payload
        except (json.JSONDecodeError, OSError):
            pass
    reg = default_registry()
    if create:
        save_registry(reg)
    return reg


def save_registry(registry: dict[str, Any]) -> Path:
    path = registry_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": _REGISTRY_VERSION,
        "entities": [_validate_entity(e) for e in registry.get("entities") or []],
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    if not payload["entities"]:
        payload["entities"] = [default_entity()]
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return path


def get_entity(entity_id: str, *, registry: dict[str, Any] | None = None) -> dict[str, Any] | None:
    reg = registry or load_registry(create=False)
    key = entity_id.strip().upper()
    for entity in reg.get("entities") or []:
        if str(entity.get("id") or "").upper() == key:
            return entity
    return None


def is_capture_enabled(entity_id: str) -> bool:
    entity = get_entity(entity_id)
    return bool(entity and entity.get("capture_enabled"))


def update_entity(entity_id: str, patch: dict[str, Any]) -> dict[str, Any]:
    reg = load_registry(create=True)
    key = entity_id.strip().upper()
    entities = list(reg.get("entities") or [])
    updated: dict[str, Any] | None = None
    for idx, entity in enumerate(entities):
        if str(entity.get("id") or "").upper() == key:
            merged = _validate_entity({**entity, **patch, "id": key})
            entities[idx] = merged
            updated = merged
            break
    if updated is None:
        updated = _validate_entity({**default_entity(), **patch, "id": key})
        entities.append(updated)
    reg["entities"] = entities
    save_registry(reg)
    return updated


def _count_glob(directory: Path, pattern: str) -> int:
    if not directory.is_dir():
        return 0
    return sum(1 for _ in directory.glob(pattern))


def _parquet_rows(path: Path) -> int:
    if not path.is_file():
        return 0
    try:
        import pandas as pd

        return int(len(pd.read_parquet(path)))
    except Exception:
        csv_path = path.with_suffix(".csv")
        if csv_path.is_file():
            try:
                return max(0, sum(1 for _ in csv_path.open(encoding="utf-8")) - 1)
            except OSError:
                return 0
    return 0


def _latest_mtime(directory: Path) -> str | None:
    if not directory.is_dir():
        return None
    latest: float | None = None
    for path in directory.rglob("*"):
        if path.is_file() and path.suffix in {".parquet", ".csv", ".json"}:
            mtime = path.stat().st_mtime
            if latest is None or mtime > latest:
                latest = mtime
    if latest is None:
        return None
    return datetime.fromtimestamp(latest, tz=timezone.utc).isoformat()


def capture_base_dir(entity_id: str) -> Path:
    return get_hub_dir() / "_data" / "capture" / entity_id.strip().lower()


def build_capture_stats(entity_id: str = _DEFAULT_ENTITY_ID) -> dict[str, Any]:
    """Row counts and last capture time per capture series."""
    hub = get_hub_dir()
    entity = entity_id.strip().upper()
    base = capture_base_dir(entity)
    poi = hub / "_data" / "participant_oi"

    series: dict[str, Any] = {}
    specs = {
        "derivatives_chain": base / "derivatives_chain",
        "flows": base / "flows",
        "vix": base / "vix",
        "quotes": base / "quotes",
        "news": base / "news",
        "participant_oi": poi,
    }
    total_rows = 0
    for name, directory in specs.items():
        days = _count_glob(directory, "*.parquet") or _count_glob(directory, "*.json")
        rows = 0
        if directory.is_dir():
            for path in sorted(directory.glob("*.parquet")):
                rows += _parquet_rows(path)
            if name == "participant_oi":
                rows = _count_glob(directory, "*.json")
        series[name] = {
            "path": str(directory.relative_to(hub)) if directory.exists() else str(directory),
            "days": days,
            "rows": rows,
            "last_capture_at": _latest_mtime(directory),
        }
        total_rows += rows

    entity_cfg = get_entity(entity) or default_entity()
    channel_stats: dict[str, Any] = {}
    try:
        from trade_integrations.hub_capture.channel import channel_stats_today

        channel_stats = channel_stats_today()
    except Exception:
        pass
    return {
        "entity_id": entity,
        "capture_enabled": bool(entity_cfg.get("capture_enabled")),
        "series": series,
        "total_rows": total_rows,
        "channel": channel_stats,
    }
