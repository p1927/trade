"""HTTP/MCP-facing watch registry operations."""

from __future__ import annotations

from typing import Any

from trade_integrations.watch_registry.scope import (
    OWNER_KIND_AUTONOMOUS,
    OWNER_KIND_SESSION,
    nautilus_owner_id,
)
from trade_integrations.watch_registry.store import (
    WatchMutationResult,
    create_watch,
    delete_watch,
    list_watches,
    update_watch,
)


def _status_from_nautilus_sync(sync: dict[str, Any]) -> str:
    status = str(sync.get("status") or "")
    if sync.get("nautilus_ok") is False or status in {"partial", "skipped"}:
        return "partial"
    if status == "error":
        return "error"
    return "ok"


def _mutation_response(
    mutation: WatchMutationResult,
    *,
    owner_kind: str | None = None,
    owner_id: str | None = None,
) -> dict[str, Any]:
    watch = mutation["watch"]
    sync = mutation["nautilus_sync"]
    payload: dict[str, Any] = {
        "status": _status_from_nautilus_sync(sync),
        "watch": watch,
        "nautilus_sync": sync,
    }
    kind = owner_kind or str(watch.get("owner_kind") or "")
    oid = owner_id or str(watch.get("owner_id") or "")
    if kind and oid:
        payload["nautilus_owner_id"] = nautilus_owner_id(owner_kind=kind, owner_id=oid)
    return payload


def mcp_create_watch(
    *,
    owner_kind: str,
    owner_id: str,
    vibe_session_id: str,
    watch_spec: dict[str, Any],
    symbols: list[str] | None = None,
    label: str | None = None,
    one_shot: bool = False,
) -> dict[str, Any]:
    try:
        mutation = create_watch(
            owner_kind=owner_kind,
            owner_id=owner_id,
            vibe_session_id=vibe_session_id,
            watch_spec=watch_spec,
            symbols=symbols,
            label=label,
            one_shot=one_shot,
        )
    except ValueError as exc:
        return {"status": "error", "error": str(exc)}
    return _mutation_response(mutation, owner_kind=owner_kind, owner_id=owner_id)


def mcp_list_watches(
    *,
    owner_kind: str | None = None,
    owner_id: str | None = None,
    session_id: str | None = None,
    agent_id: str | None = None,
) -> dict[str, Any]:
    kind = owner_kind
    oid = owner_id
    if not oid and agent_id:
        kind = OWNER_KIND_AUTONOMOUS
        oid = agent_id
    elif not oid and session_id:
        kind = OWNER_KIND_SESSION
        oid = session_id
    rows = list_watches(owner_kind=kind, owner_id=oid, active_only=True)
    if kind == OWNER_KIND_AUTONOMOUS and oid and not rows:
        try:
            from trade_integrations.watch_registry.store import migrate_agent_watch_spec_to_registry

            migrate_agent_watch_spec_to_registry(oid)
            rows = list_watches(owner_kind=kind, owner_id=oid, active_only=True)
        except Exception:
            pass
    return {"status": "ok", "watches": rows, "count": len(rows)}


def mcp_delete_watch(watch_id: str) -> dict[str, Any]:
    if not watch_id:
        return {"status": "error", "error": "watch_id required"}
    mutation = delete_watch(watch_id)
    if not mutation:
        return {"status": "error", "error": f"watch not found: {watch_id}"}
    response = _mutation_response(mutation)
    response["watch_id"] = watch_id
    return response


def mcp_update_watch(
    watch_id: str,
    *,
    watch_spec: dict[str, Any] | None = None,
    label: str | None = None,
) -> dict[str, Any]:
    mutation = update_watch(watch_id, watch_spec=watch_spec, label=label)
    if not mutation:
        return {"status": "error", "error": f"watch not found: {watch_id}"}
    return _mutation_response(mutation)


def resolve_owner_for_session(session_id: str) -> tuple[str, str]:
    return OWNER_KIND_SESSION, session_id


def resolve_owner_for_agent(agent_id: str) -> tuple[str, str]:
    return OWNER_KIND_AUTONOMOUS, agent_id
