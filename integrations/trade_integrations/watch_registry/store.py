"""Persist and sync unified watch registrations."""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from trade_integrations.context.hub import get_hub_dir
from trade_integrations.watch_registry.scope import (
    OWNER_KIND_AUTONOMOUS,
    OWNER_KIND_SESSION,
    combined_watch_spec_for_owner,
    nautilus_owner_id,
    parse_nautilus_owner_id,
    symbols_for_owner,
    symbols_for_watch,
)

logger = logging.getLogger(__name__)

_WATCH_DIR = "watches"
_INDEX_FILE = "index.json"


def _watches_root() -> Path:
    root = get_hub_dir() / "_data" / _WATCH_DIR
    root.mkdir(parents=True, exist_ok=True)
    return root


def _watch_path(watch_id: str) -> Path:
    safe = "".join(c for c in watch_id if c.isalnum() or c in "_-")
    return _watches_root() / f"{safe}.json"


def _index_path() -> Path:
    return _watches_root() / _INDEX_FILE


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_watch_id() -> str:
    return f"w_{uuid.uuid4().hex[:12]}"


def _load_index() -> dict[str, Any]:
    path = _index_path()
    if not path.is_file():
        return {"owners": {}, "updated_at": None}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"owners": {}, "updated_at": None}
    if not isinstance(payload, dict):
        return {"owners": {}, "updated_at": None}
    owners = payload.get("owners")
    if not isinstance(owners, dict):
        owners = {}
    return {"owners": owners, "updated_at": payload.get("updated_at")}


def _save_index(index: dict[str, Any]) -> None:
    index = dict(index)
    index["updated_at"] = _now_iso()
    _index_path().write_text(json.dumps(index, indent=2), encoding="utf-8")


def _owner_key(owner_kind: str, owner_id: str) -> str:
    return f"{owner_kind}:{owner_id}"


def _read_watch(watch_id: str) -> dict[str, Any] | None:
    path = _watch_path(watch_id)
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _write_watch(watch: dict[str, Any]) -> dict[str, Any]:
    watch_id = str(watch.get("watch_id") or "").strip()
    if not watch_id:
        raise ValueError("watch_id required")
    _watch_path(watch_id).write_text(json.dumps(watch, indent=2), encoding="utf-8")
    return watch


def get_watch(watch_id: str) -> dict[str, Any] | None:
    return _read_watch(str(watch_id or "").strip())


def list_watches(
    *,
    owner_kind: str | None = None,
    owner_id: str | None = None,
    active_only: bool = True,
) -> list[dict[str, Any]]:
    index = _load_index()
    owners = index.get("owners") or {}
    out: list[dict[str, Any]] = []
    if owner_kind and owner_id:
        keys = [_owner_key(owner_kind, owner_id)]
    else:
        keys = list(owners.keys())
    for key in keys:
        watch_ids = owners.get(key) or []
        if not isinstance(watch_ids, list):
            continue
        for wid in watch_ids:
            watch = _read_watch(str(wid))
            if not watch:
                continue
            if active_only and str(watch.get("status") or "active") != "active":
                continue
            out.append(watch)
    return out


def list_watches_for_nautilus_owner(nautilus_owner: str) -> list[dict[str, Any]]:
    kind, oid = parse_nautilus_owner_id(nautilus_owner)
    return list_watches(owner_kind=kind, owner_id=oid, active_only=False)


def create_watch(
    *,
    owner_kind: str,
    owner_id: str,
    vibe_session_id: str,
    watch_spec: dict[str, Any],
    symbols: list[str] | None = None,
    label: str | None = None,
    one_shot: bool = False,
) -> dict[str, Any]:
    owner_kind = str(owner_kind or "").strip().lower()
    owner_id = str(owner_id or "").strip()
    if owner_kind not in {OWNER_KIND_SESSION, OWNER_KIND_AUTONOMOUS}:
        raise ValueError(f"invalid owner_kind: {owner_kind}")
    if not owner_id:
        raise ValueError("owner_id required")
    if not vibe_session_id:
        raise ValueError("vibe_session_id required")
    spec = dict(watch_spec or {})
    if not spec.get("rules"):
        raise ValueError("watch_spec.rules required")

    now = _now_iso()
    watch_id = new_watch_id()
    sym_list = [str(s).upper() for s in (symbols or []) if str(s).strip()]
    if not sym_list:
        sym_list = list(symbols_for_watch({"watch_spec": spec, "symbols": []}))

    watch = {
        "watch_id": watch_id,
        "owner_kind": owner_kind,
        "owner_id": owner_id,
        "vibe_session_id": str(vibe_session_id),
        "label": str(label or "").strip() or None,
        "symbols": sym_list,
        "watch_spec": spec,
        "status": "active",
        "one_shot": bool(one_shot),
        "created_at": now,
        "updated_at": now,
        "last_fired_at": None,
        "last_alert_message": None,
    }
    _write_watch(watch)

    index = _load_index()
    owners = dict(index.get("owners") or {})
    key = _owner_key(owner_kind, owner_id)
    ids = list(owners.get(key) or [])
    ids.append(watch_id)
    owners[key] = ids
    index["owners"] = owners
    _save_index(index)

    _sync_owner_handoff(owner_kind, owner_id)
    sync_nautilus_registry_from_watches(restart_if_changed=True)
    return watch


def update_watch(watch_id: str, *, watch_spec: dict[str, Any] | None = None, label: str | None = None) -> dict[str, Any] | None:
    watch = _read_watch(watch_id)
    if not watch or str(watch.get("status")) == "deleted":
        return None
    if watch_spec is not None:
        watch["watch_spec"] = dict(watch_spec)
        watch["symbols"] = list(symbols_for_watch(watch))
    if label is not None:
        watch["label"] = str(label).strip() or None
    watch["updated_at"] = _now_iso()
    _write_watch(watch)
    _sync_owner_handoff(str(watch.get("owner_kind") or ""), str(watch.get("owner_id") or ""))
    sync_nautilus_registry_from_watches(restart_if_changed=True)
    return watch


def delete_watch(watch_id: str) -> bool:
    watch = _read_watch(watch_id)
    if not watch:
        return False
    watch["status"] = "deleted"
    watch["updated_at"] = _now_iso()
    _write_watch(watch)

    index = _load_index()
    owners = dict(index.get("owners") or {})
    key = _owner_key(str(watch.get("owner_kind") or ""), str(watch.get("owner_id") or ""))
    ids = [wid for wid in (owners.get(key) or []) if str(wid) != watch_id]
    if ids:
        owners[key] = ids
    else:
        owners.pop(key, None)
    index["owners"] = owners
    _save_index(index)

    _sync_owner_handoff(str(watch.get("owner_kind") or ""), str(watch.get("owner_id") or ""))
    sync_nautilus_registry_from_watches(restart_if_changed=True)
    return True


def delete_watches_for_owner(*, owner_kind: str, owner_id: str) -> int:
    watches = list_watches(owner_kind=owner_kind, owner_id=owner_id, active_only=False)
    removed = 0
    for watch in watches:
        if str(watch.get("status")) != "deleted" and delete_watch(str(watch.get("watch_id"))):
            removed += 1
    return removed


def record_watch_fired(watch_id: str, message: str) -> dict[str, Any] | None:
    watch = _read_watch(watch_id)
    if not watch:
        return None
    watch["last_fired_at"] = _now_iso()
    watch["last_alert_message"] = str(message or "")[:500]
    watch["updated_at"] = _now_iso()
    _write_watch(watch)
    if watch.get("one_shot"):
        delete_watch(watch_id)
    return watch


def _agent_eligible_for_nautilus_registry(agent: dict[str, Any]) -> bool:
    """Running agents, or infra-paused plan-approved agents with active registry watches."""
    status = str(agent.get("status") or "")
    if status == "running":
        return True
    if status == "paused" and str(agent.get("pause_reason") or "") == "infra":
        try:
            from trade_integrations.autonomous_agents.plan_approval import is_plan_approved

            return is_plan_approved(agent)
        except Exception:
            return False
    return False


def list_active_nautilus_owners() -> list[dict[str, Any]]:
    """Owners with at least one active watch and non-empty symbol scope."""
    index = _load_index()
    owners = index.get("owners") or {}
    rows: list[dict[str, Any]] = []
    for key, watch_ids in owners.items():
        if not isinstance(watch_ids, list):
            continue
        active = []
        for wid in watch_ids:
            watch = _read_watch(str(wid))
            if watch and str(watch.get("status") or "active") == "active":
                active.append(watch)
        if not active:
            continue
        if ":" not in key:
            continue
        kind, oid = key.split(":", 1)
        noid = nautilus_owner_id(owner_kind=kind, owner_id=oid)
        from trade_integrations.watch_registry.scope import symbols_for_owner

        symbol_list = list(symbols_for_owner(noid, watches=active))
        if not symbol_list:
            continue
        market = "IN"
        if kind == OWNER_KIND_AUTONOMOUS:
            try:
                from trade_integrations.autonomous_agents.store import get_agent
                from trade_integrations.execution.routing_context import resolve_agent_routing

                agent = get_agent(oid) or {}
                if not agent or not _agent_eligible_for_nautilus_registry(agent):
                    continue
                market = resolve_agent_routing(agent).market
            except Exception:
                continue
        else:
            try:
                from trade_integrations.autonomous_agents.market import symbol_execution_market

                for sym in symbol_list:
                    if symbol_execution_market(sym) == "US":
                        market = "US"
                        break
            except Exception:
                pass
        rows.append(
            {
                "agent_id": noid,
                "owner_kind": kind,
                "owner_id": oid,
                "market": market,
                "symbols": symbol_list,
                "vibe_session_id": active[0].get("vibe_session_id"),
            }
        )
    return rows


def _sync_owner_handoff(owner_kind: str, owner_id: str) -> None:
    noid = nautilus_owner_id(owner_kind=owner_kind, owner_id=owner_id)
    spec = combined_watch_spec_for_owner(noid)
    try:
        from nautilus_openalgo_bridge.handoff import load_handoff, save_handoff
        from nautilus_openalgo_bridge.models import PositionHandoff, StopRules, WatchSpec

        existing = load_handoff(noid)
        ws = WatchSpec.from_dict(spec)
        if existing:
            existing.watch_spec = ws
            save_handoff(existing)
            return
        if owner_kind == OWNER_KIND_AUTONOMOUS:
            from nautilus_openalgo_bridge.handoff import build_handoff_shell_from_hub_agent

            shell = build_handoff_shell_from_hub_agent(owner_id)
            if shell:
                shell.agent_id = noid
                shell.watch_spec = ws
                save_handoff(shell)
                return
        syms = list(symbols_for_owner(noid))
        underlying = syms[0] if syms else "NIFTY"
        watches = list_watches(owner_kind=owner_kind, owner_id=owner_id, active_only=True)
        vibe_sid = watches[0].get("vibe_session_id") if watches else None
        save_handoff(
            PositionHandoff(
                agent_id=noid,
                widget_id=None,
                underlying=underlying,
                legs=[],
                entry_spot=0.0,
                watch_spec=ws,
                stop_rules=StopRules(),
                vibe_session_id=vibe_sid,
            )
        )
    except Exception as exc:
        logger.warning("handoff sync skipped for %s: %s", noid, exc)


def migrate_agent_watch_spec_to_registry(agent_id: str) -> dict[str, Any] | None:
    """Create registry watch from legacy agent.watch_spec if none exist."""
    agent_id = str(agent_id or "").strip()
    if not agent_id:
        return None
    existing = list_watches(owner_kind=OWNER_KIND_AUTONOMOUS, owner_id=agent_id, active_only=True)
    if existing:
        return existing[0]
    try:
        from trade_integrations.autonomous_agents.store import get_agent

        agent = get_agent(agent_id) or {}
    except Exception:
        return None
    if not agent:
        return None
    raw = agent.get("watch_spec") or (agent.get("mandate_config") or {}).get("watch_spec")
    if not isinstance(raw, dict) or not raw.get("rules"):
        return None
    vibe_sid = str(agent.get("vibe_session_id") or "")
    if not vibe_sid:
        return None
    return create_watch(
        owner_kind=OWNER_KIND_AUTONOMOUS,
        owner_id=agent_id,
        vibe_session_id=vibe_sid,
        watch_spec=raw,
        symbols=list(agent.get("symbols") or []),
        label="strategy watch",
    )


def record_owner_alert_fired(
    nautilus_owner: str,
    message: str,
    *,
    symbol: str | None = None,
) -> list[str]:
    """Mark matching active watches as fired; returns watch_ids updated."""
    from trade_integrations.watch_registry.scope import parse_nautilus_owner_id, symbols_for_watch

    kind, oid = parse_nautilus_owner_id(nautilus_owner)
    updated: list[str] = []
    for watch in list_watches(owner_kind=kind, owner_id=oid, active_only=True):
        if symbol:
            sym = str(symbol).strip().upper()
            if sym and sym not in symbols_for_watch(watch):
                continue
        wid = str(watch.get("watch_id") or "")
        if wid and record_watch_fired(wid, message):
            updated.append(wid)
    return updated


def sync_nautilus_registry_from_watches(*, restart_if_changed: bool = False) -> dict[str, Any]:
    """Rebuild log/nautilus-watch.agents.json from active watch owners."""
    try:
        from trade_integrations.autonomous_agents import nautilus_watch as nw
    except ImportError:
        return {"status": "skipped", "reason": "nautilus_watch unavailable"}

    rows = list_active_nautilus_owners()
    registry = nw.load_registry()
    old_ids = sorted(nw.get_registry_agent_ids())
    old_agents = list(registry.get("agents") or [])

    def _symbol_signature(agent_rows: list[dict[str, Any]]) -> tuple[tuple[str, tuple[str, ...]], ...]:
        return tuple(
            sorted(
                (
                    str(row.get("agent_id") or ""),
                    tuple(sorted(str(s).upper() for s in (row.get("symbols") or []) if str(s).strip())),
                )
                for row in agent_rows
                if row.get("agent_id")
            )
        )

    old_sig = _symbol_signature(old_agents)
    agents = []
    now = _now_iso()
    for row in rows:
        agents.append(
            {
                "agent_id": row["agent_id"],
                "market": row.get("market") or "IN",
                "symbols": row.get("symbols") or [],
                "bound_at": now,
                "owner_kind": row.get("owner_kind"),
                "vibe_session_id": row.get("vibe_session_id"),
            }
        )
    registry["agents"] = agents
    registry["node_agent_ids"] = sorted(str(r["agent_id"]) for r in rows)
    nw.save_registry(registry)
    new_ids = sorted(nw.get_registry_agent_ids())
    new_sig = _symbol_signature(agents)

    for row in rows:
        _sync_owner_handoff(str(row.get("owner_kind") or ""), str(row.get("owner_id") or ""))

    if restart_if_changed and (old_ids != new_ids or old_sig != new_sig):
        live_pid = nw._read_pid()
        if live_pid is not None and nw._process_alive(live_pid):
            logger.info("watch registry changed %s → %s — restarting Nautilus", old_ids, new_ids)
            nw._stop_existing()
            try:
                nw._launch_watch(use_registry=True)
            except Exception:
                logger.exception("failed to relaunch Nautilus after watch registry sync")
    return {"status": "ok", "owners": len(agents), "agent_ids": new_ids}
